"""Real Notion/Drive adapters behind the SyncClient protocol. Tests use fakes;
these are exercised by the manual SMOKE.md checklist."""

import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from brain.db import parse_ts
from brain.mdblocks import blocks_to_md, md_to_blocks
from brain.sync import RateLimited, RemoteItem

NOTION_VERSION = "2026-03-11"
MAX_BLOCKS_PER_REQUEST = 100  # Notion caps block children per request
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]  # full drive: must see files humans drop in
MD_MIME = "text/markdown"
GDOC_MIME = "application/vnd.google-apps.document"


class NotionRemote:
    provider = "notion"

    def __init__(self, token: str):
        from notion_client import Client  # ponytail: lazy import keeps tests offline

        self.api = Client(auth=token, notion_version=NOTION_VERSION)
        self._data_sources: dict[str, str] = {}

    def _data_source_id(self, database_id: str) -> str:
        if database_id not in self._data_sources:
            db: Any = self._wrap(self.api.databases.retrieve, database_id=database_id)
            self._data_sources[database_id] = db["data_sources"][0]["id"]
        return self._data_sources[database_id]

    def _wrap(self, fn: Any, **kwargs: Any) -> Any:
        from notion_client.errors import APIResponseError

        try:
            return fn(**kwargs)
        except APIResponseError as exc:
            if exc.status == 429:
                raise RateLimited(float(exc.headers.get("Retry-After", 1))) from exc
            raise

    def _page_title(self, page: dict) -> str:
        for prop in page["properties"].values():
            if prop["type"] == "title":
                return "".join(span["plain_text"] for span in prop["title"])
        return ""

    def _page_body(self, page_id: str) -> str:
        blocks, cursor = [], None
        while True:
            resp: Any = self._wrap(self.api.blocks.children.list, block_id=page_id, start_cursor=cursor, page_size=100)
            blocks.extend(resp["results"])
            if not resp["has_more"]:
                return blocks_to_md(blocks)
            cursor = resp["next_cursor"]

    def list_changes(self, target_id: str, cursor: str | None) -> tuple[list[RemoteItem], str, list[str]]:
        # Full listing every sync (ponytail: fine at note scale — 1 request/100 pages). The
        # query silently omits trashed/moved-out pages, so the id listing is the only delete
        # signal; the engine reconciles against it. Bodies are fetched only for pages edited
        # since cursor-1min (last_edited_time is minute-rounded; engine dedupes echoes by hash).
        ds_id = self._data_source_id(target_id)
        since = (parse_ts(cursor) - timedelta(minutes=1)) if cursor else None
        query: dict[str, Any] = {"sorts": [{"timestamp": "last_edited_time", "direction": "ascending"}]}
        items: list[RemoteItem] = []
        present: list[str] = []
        start_cursor = None
        new_cursor = cursor or datetime.now(UTC).isoformat()
        while True:
            resp: Any = self._wrap(
                self.api.request,
                path=f"data_sources/{ds_id}/query",
                method="POST",
                body={**query, "start_cursor": start_cursor} if start_cursor else query,
            )
            for page in resp["results"]:
                present.append(page["id"])
                edited = page["last_edited_time"]
                if since is None or parse_ts(edited) >= since:
                    items.append(
                        RemoteItem(
                            remote_id=page["id"],
                            title=self._page_title(page),
                            body_md=self._page_body(page["id"]),
                            updated_at=edited,
                            trashed=page.get("in_trash", False),
                        )
                    )
                if parse_ts(edited) > parse_ts(new_cursor):
                    new_cursor = edited
            if not resp["has_more"]:
                return items, new_cursor, present
            start_cursor = resp["next_cursor"]

    def fetch(self, remote_id: str) -> RemoteItem:
        page: Any = self._wrap(self.api.pages.retrieve, page_id=remote_id)
        return RemoteItem(
            remote_id=page["id"],
            title=self._page_title(page),
            body_md=self._page_body(page["id"]),
            updated_at=page["last_edited_time"],
            trashed=page.get("in_trash", False),
        )

    def _append_blocks(self, page_id: str, blocks: list[dict]) -> None:
        for start in range(0, len(blocks), MAX_BLOCKS_PER_REQUEST):
            self._wrap(self.api.blocks.children.append, block_id=page_id, children=blocks[start : start + MAX_BLOCKS_PER_REQUEST])

    def create(self, target_id: str, title: str, body_md: str) -> RemoteItem:
        ds_id = self._data_source_id(target_id)
        blocks = md_to_blocks(body_md)
        page: Any = self._wrap(
            self.api.pages.create,
            parent={"data_source_id": ds_id},
            properties={"title": {"title": [{"type": "text", "text": {"content": title}}]}},
            children=blocks[:MAX_BLOCKS_PER_REQUEST],
        )
        self._append_blocks(page["id"], blocks[MAX_BLOCKS_PER_REQUEST:])
        return RemoteItem(remote_id=page["id"], title=title, body_md=body_md, updated_at=page["last_edited_time"])

    def update(self, remote_id: str, title: str, body_md: str) -> RemoteItem:
        page: Any = self._wrap(
            self.api.pages.update, page_id=remote_id, properties={"title": {"title": [{"type": "text", "text": {"content": title}}]}}
        )
        # ponytail: replace-all body update (delete old blocks, append new); fine at note scale
        while True:
            existing: Any = self._wrap(self.api.blocks.children.list, block_id=remote_id, page_size=100)
            for block in existing["results"]:
                self._wrap(self.api.blocks.delete, block_id=block["id"])
            if not existing["has_more"]:
                break
        self._append_blocks(remote_id, md_to_blocks(body_md))
        return RemoteItem(remote_id=remote_id, title=title, body_md=body_md, updated_at=page["last_edited_time"])

    def delete(self, remote_id: str) -> None:
        self._wrap(self.api.pages.update, page_id=remote_id, in_trash=True)


class DriveRemote:
    provider = "drive"

    def __init__(self, token_path: Path):
        from google.oauth2.credentials import Credentials

        creds = Credentials.from_authorized_user_info(json.loads(token_path.read_text()), DRIVE_SCOPES)
        if creds.expired and creds.refresh_token:
            from google.auth.transport.requests import Request

            creds.refresh(Request())
            token_path.write_text(creds.to_json())  # google-auth has no token store; persist ourselves
        from googleapiclient.discovery import build

        self.api = build("drive", "v3", credentials=creds)

    def _execute(self, request: Any) -> Any:
        from googleapiclient.errors import HttpError

        try:
            return request.execute()
        except HttpError as exc:
            if exc.resp.status == 429:
                raise RateLimited(float(exc.resp.get("retry-after", 1))) from exc
            raise

    def _to_item(self, meta: dict, trashed: bool = False) -> RemoteItem:
        if trashed:
            return RemoteItem(remote_id=meta["id"], title="", body_md="", updated_at=meta.get("modifiedTime", ""), trashed=True)
        if meta["mimeType"] == GDOC_MIME:
            # native Docs: export as markdown, import read-only (we never write markdown back into a Doc)
            body = self._execute(self.api.files().export(fileId=meta["id"], mimeType=MD_MIME)).decode()
        else:
            body = self._execute(self.api.files().get_media(fileId=meta["id"])).decode()
        title = meta["name"].removesuffix(".md")
        return RemoteItem(remote_id=meta["id"], title=title, body_md=body, updated_at=meta["modifiedTime"])

    def list_changes(self, target_id: str, cursor: str | None) -> tuple[list[RemoteItem], str, None]:
        # Returns None for present-ids: the changes feed already reports deletes/trashes.
        fields = "id, name, mimeType, md5Checksum, modifiedTime, trashed, parents"
        items = []
        if cursor is None:  # initial import: files.list on the folder; changes feed from now on
            token = self._execute(self.api.changes().getStartPageToken())["startPageToken"]
            page_token = None
            while True:
                resp = self._execute(
                    self.api.files().list(
                        q=f"'{target_id}' in parents and trashed = false", fields=f"nextPageToken, files({fields})", pageToken=page_token
                    )
                )
                items.extend(self._to_item(meta) for meta in resp.get("files", []))
                page_token = resp.get("nextPageToken")
                if not page_token:
                    return items, token, None
        token = cursor
        while True:
            resp = self._execute(
                self.api.changes().list(pageToken=token, fields=f"nextPageToken, newStartPageToken, changes(removed, fileId, file({fields}))")
            )
            for change in resp.get("changes", []):
                meta = change.get("file") or {"id": change["fileId"]}
                if change.get("removed") or meta.get("trashed"):
                    items.append(self._to_item(meta, trashed=True))
                elif target_id in meta.get("parents", []):  # global feed -> keep only our folder
                    items.append(self._to_item(meta))
            token = resp.get("nextPageToken") or resp["newStartPageToken"]
            if "newStartPageToken" in resp:
                return items, token, None

    def fetch(self, remote_id: str) -> RemoteItem:
        meta = self._execute(self.api.files().get(fileId=remote_id, fields="id, name, mimeType, md5Checksum, modifiedTime, trashed"))
        return self._to_item(meta, trashed=meta.get("trashed", False))

    def _media(self, body_md: str) -> Any:
        from googleapiclient.http import MediaIoBaseUpload

        return MediaIoBaseUpload(io.BytesIO(body_md.encode()), mimetype=MD_MIME)

    def create(self, target_id: str, title: str, body_md: str) -> RemoteItem:
        meta = self._execute(
            self.api.files().create(
                body={"name": f"{title}.md", "parents": [target_id], "mimeType": MD_MIME},
                media_body=self._media(body_md),
                fields="id, modifiedTime",
            )
        )
        return RemoteItem(remote_id=meta["id"], title=title, body_md=body_md, updated_at=meta["modifiedTime"])

    def update(self, remote_id: str, title: str, body_md: str) -> RemoteItem:
        meta = self._execute(
            self.api.files().update(fileId=remote_id, body={"name": f"{title}.md"}, media_body=self._media(body_md), fields="id, modifiedTime")
        )
        return RemoteItem(remote_id=remote_id, title=title, body_md=body_md, updated_at=meta["modifiedTime"])

    def delete(self, remote_id: str) -> None:
        self._execute(self.api.files().update(fileId=remote_id, body={"trashed": True}))

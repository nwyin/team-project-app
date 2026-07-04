"""Two-way sync engine: pull remote changes since cursor, resolve conflicts (LWW,
ties remote-wins), then push local changes. Content hashes make unchanged items no-ops."""

import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from brain.db import brain_dir, parse_ts
from brain.repo import content_hash, record_history, update_item


@dataclass
class RemoteItem:
    remote_id: str
    title: str
    body_md: str
    updated_at: str  # ISO UTC
    trashed: bool = False
    read_only: bool = False


class SyncClient(Protocol):
    provider: str

    def list_changes(self, target_id: str, cursor: str | None) -> tuple[list[RemoteItem], str, list[str] | None]:
        """Changes since cursor (everything on first sync), the new cursor, and — if the
        provider can enumerate the target cheaply — ALL remote ids currently present.
        Notion returns the full id listing (its query silently drops trashed/moved-out pages,
        so absence is the only delete signal); Drive returns None (its changes feed already
        reports deletes)."""
        ...

    def fetch(self, remote_id: str) -> RemoteItem:
        """Fetch one item by id — used for pages that are present but predate the cursor
        (e.g. an old page moved into the database without a fresh edit)."""
        ...

    def create(self, target_id: str, title: str, body_md: str) -> RemoteItem: ...

    def update(self, remote_id: str, title: str, body_md: str) -> RemoteItem: ...

    def delete(self, remote_id: str) -> None: ...


class RateLimited(Exception):
    def __init__(self, retry_after: float = 1.0):
        super().__init__(f"rate limited, retry after {retry_after}s")
        self.retry_after = retry_after


def call_with_retry(fn: Callable[..., Any], *args: Any, attempts: int = 5, sleep: Callable[[float], None] = time.sleep, **kwargs: Any) -> Any:
    for attempt in range(attempts):
        try:
            return fn(*args, **kwargs)
        except RateLimited as exc:
            if attempt == attempts - 1:
                raise
            sleep(exc.retry_after)


def sync_provider(conn: sqlite3.Connection, client: SyncClient) -> dict:
    """Sync every space that has a target for this client's provider."""
    stats = {"pulled": 0, "pushed": 0, "archived": 0, "deleted": 0, "uploaded": 0}
    targets = conn.execute("SELECT * FROM sync_targets WHERE provider = ?", (client.provider,)).fetchall()
    cursors: list[tuple[int, str]] = []
    for target in targets:
        changes, new_cursor, present = call_with_retry(client.list_changes, target["remote_id"], target["cursor"])
        for remote in changes:
            _apply_remote(conn, client.provider, target["space_id"], remote, stats)
        if present is not None:
            _reconcile_presence(conn, client, target["space_id"], set(present), {r.remote_id for r in changes}, stats)
        cursors.append((target["id"], new_cursor))

    for target in targets:
        _cleanup_stale_mappings(conn, client, target["space_id"], stats)
    for target in targets:
        _push_local(conn, client, target["space_id"], target["remote_id"], stats)
    for target_id, new_cursor in cursors:
        conn.execute("UPDATE sync_targets SET cursor = ? WHERE id = ?", (new_cursor, target_id))
    conn.commit()
    return stats


def _reconcile_presence(conn: sqlite3.Connection, client: SyncClient, space_id: int, present: set[str], changed: set[str], stats: dict) -> None:
    """Two-way repair against the full remote listing: mapped ids that vanished remotely
    are archived locally; present ids we've never mapped (and that didn't surface as
    changes, e.g. old pages moved in) are fetched and ingested."""
    mapped = conn.execute(
        "SELECT m.remote_id FROM item_remote m WHERE m.provider = ? AND m.space_id = ?", (client.provider, space_id)
    ).fetchall()
    for row in mapped:
        if row["remote_id"] not in present:
            _apply_remote(conn, client.provider, space_id, RemoteItem(row["remote_id"], "", "", "", trashed=True), stats)
    for remote_id in present - changed:
        if _mapping(conn, client.provider, remote_id) is None:
            _apply_remote(conn, client.provider, space_id, call_with_retry(client.fetch, remote_id), stats)


def _mapping(conn: sqlite3.Connection, provider: str, remote_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM item_remote WHERE provider = ? AND remote_id = ?", (provider, remote_id)).fetchone()


def _set_mapping(conn: sqlite3.Connection, item_id: int, provider: str, space_id: int, remote: RemoteItem, digest: str) -> None:
    conn.execute(
        "INSERT INTO item_remote (item_id, provider, remote_id, content_hash, remote_updated_at, read_only, space_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (item_id, provider) DO UPDATE SET remote_id = excluded.remote_id, "
        "content_hash = excluded.content_hash, remote_updated_at = excluded.remote_updated_at, "
        "read_only = excluded.read_only, space_id = excluded.space_id",
        (item_id, provider, remote.remote_id, digest, remote.updated_at, int(remote.read_only), space_id),
    )


def _apply_remote(conn: sqlite3.Connection, provider: str, space_id: int, remote: RemoteItem, stats: dict) -> None:
    mapping = _mapping(conn, provider, remote.remote_id)
    if mapping is not None:
        item = conn.execute("SELECT * FROM items WHERE id = ?", (mapping["item_id"],)).fetchone()
        if item["space_id"] != space_id:
            return
    if remote.trashed:
        if mapping is not None:
            if not item["archived"]:
                record_history(conn, item["id"], item["title"], item["body"], item["updated_at"], "remote-delete")
                conn.execute("UPDATE items SET archived = 1 WHERE id = ?", (item["id"],))
                stats["archived"] += 1
            conn.execute("DELETE FROM item_remote WHERE id = ?", (mapping["id"],))
        return

    remote_hash = content_hash(remote.title, remote.body_md)
    if mapping is None:
        cur = conn.execute(
            "INSERT INTO items (space_id, title, body, source, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (space_id, remote.title, remote.body_md, provider, remote.updated_at, remote.updated_at),
        )
        assert cur.lastrowid is not None
        _set_mapping(conn, cur.lastrowid, provider, space_id, remote, remote_hash)
        stats["pulled"] += 1
        return

    if remote_hash == mapping["content_hash"]:  # overlap-window echo or our own push — no-op
        _set_mapping(conn, mapping["item_id"], provider, space_id, remote, remote_hash)
        return

    local_changed = content_hash(item["title"], item["body"]) != mapping["content_hash"]
    if local_changed and parse_ts(remote.updated_at) < parse_ts(item["updated_at"]):
        # local wins; remote version (the loser) goes to history, push phase overwrites remote
        record_history(conn, item["id"], remote.title, remote.body_md, remote.updated_at, "conflict-local-won")
        conn.execute(
            "UPDATE item_remote SET remote_updated_at = ?, read_only = ?, space_id = ? WHERE id = ?",
            (remote.updated_at, int(remote.read_only), space_id, mapping["id"]),
        )
        return
    if local_changed:  # remote newer or same-minute tie -> remote wins
        record_history(conn, item["id"], item["title"], item["body"], item["updated_at"], "conflict-remote-won")
    update_item(conn, item["id"], remote.title, remote.body_md, remote.updated_at)
    _set_mapping(conn, item["id"], provider, space_id, remote, remote_hash)
    stats["pulled"] += 1


def _cleanup_stale_mappings(conn: sqlite3.Connection, client: SyncClient, space_id: int, stats: dict) -> None:
    mappings = conn.execute(
        "SELECT m.* FROM item_remote m JOIN items i ON i.id = m.item_id "
        "WHERE m.provider = ? AND m.space_id = ? AND i.space_id != m.space_id AND m.read_only = 0",
        (client.provider, space_id),
    ).fetchall()
    for mapping in mappings:
        call_with_retry(client.delete, mapping["remote_id"])
        conn.execute("DELETE FROM item_remote WHERE id = ?", (mapping["id"],))
        stats["deleted"] += 1


def _push_local(conn: sqlite3.Connection, client: SyncClient, space_id: int, target_id: str, stats: dict) -> None:
    upload = getattr(client, "upload_file", None)
    items = conn.execute("SELECT * FROM items WHERE space_id = ?", (space_id,)).fetchall()
    for item in items:
        mapping = conn.execute("SELECT * FROM item_remote WHERE item_id = ? AND provider = ?", (item["id"], client.provider)).fetchone()
        if mapping is not None and mapping["read_only"]:
            # ponytail: local edits to read-only imports stay local.
            continue
        if item["archived"]:
            if mapping is not None:
                call_with_retry(client.delete, mapping["remote_id"])
                conn.execute("DELETE FROM item_remote WHERE id = ?", (mapping["id"],))
                stats["deleted"] += 1
            continue
        local_hash = content_hash(item["title"], item["body"])
        if mapping is None:
            remote = call_with_retry(client.create, target_id, item["title"], item["body"])
            _set_mapping(conn, item["id"], client.provider, space_id, remote, local_hash)
            stats["pushed"] += 1
        elif local_hash != mapping["content_hash"]:
            remote = call_with_retry(client.update, mapping["remote_id"], item["title"], item["body"])
            _set_mapping(conn, item["id"], client.provider, space_id, remote, local_hash)
            stats["pushed"] += 1
        if upload is not None:
            _upload_attachments(conn, upload, target_id, item["id"], stats)


def _upload_attachments(conn: sqlite3.Connection, upload: Callable[[str, str, bytes], str], target_id: str, item_id: int, stats: dict) -> None:
    attachments = conn.execute("SELECT * FROM attachments WHERE item_id = ? AND drive_file_id IS NULL", (item_id,)).fetchall()
    for attachment in attachments:
        path = brain_dir() / "files" / str(item_id) / attachment["filename"]
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            # ponytail: attachment rows can outlive local file-cache entries; retry on a later sync if the bytes reappear.
            continue
        filename = f"{item_id}-{attachment['filename']}"
        drive_file_id = call_with_retry(upload, target_id, filename, data)
        conn.execute("UPDATE attachments SET drive_file_id = ? WHERE id = ?", (drive_file_id, attachment["id"]))
        stats["uploaded"] += 1

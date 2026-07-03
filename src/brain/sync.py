"""Two-way sync engine: pull remote changes since cursor, resolve conflicts (LWW,
ties remote-wins), then push local changes. Content hashes make unchanged items no-ops."""

import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from brain.db import parse_ts
from brain.repo import content_hash, record_history, update_item


@dataclass
class RemoteItem:
    remote_id: str
    title: str
    body_md: str
    updated_at: str  # ISO UTC
    trashed: bool = False


class SyncClient(Protocol):
    provider: str

    def list_changes(self, target_id: str, cursor: str | None) -> tuple[list[RemoteItem], str]:
        """Changes since cursor (everything on first sync) plus the new cursor.
        Notion: last_edited_time query with 1-minute overlap. Drive: changes.list page token."""
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
    stats = {"pulled": 0, "pushed": 0, "archived": 0, "deleted": 0}
    targets = conn.execute("SELECT * FROM sync_targets WHERE provider = ?", (client.provider,)).fetchall()
    for target in targets:
        changes, new_cursor = call_with_retry(client.list_changes, target["remote_id"], target["cursor"])
        for remote in changes:
            _apply_remote(conn, client.provider, target["space_id"], remote, stats)
        _push_local(conn, client, target["space_id"], target["remote_id"], stats)
        conn.execute("UPDATE sync_targets SET cursor = ? WHERE id = ?", (new_cursor, target["id"]))
        conn.commit()
    return stats


def _mapping(conn: sqlite3.Connection, provider: str, remote_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM item_remote WHERE provider = ? AND remote_id = ?", (provider, remote_id)).fetchone()


def _set_mapping(conn: sqlite3.Connection, item_id: int, provider: str, remote: RemoteItem, digest: str) -> None:
    conn.execute(
        "INSERT INTO item_remote (item_id, provider, remote_id, content_hash, remote_updated_at) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT (item_id, provider) DO UPDATE SET remote_id = excluded.remote_id, "
        "content_hash = excluded.content_hash, remote_updated_at = excluded.remote_updated_at",
        (item_id, provider, remote.remote_id, digest, remote.updated_at),
    )


def _apply_remote(conn: sqlite3.Connection, provider: str, space_id: int, remote: RemoteItem, stats: dict) -> None:
    mapping = _mapping(conn, provider, remote.remote_id)
    if remote.trashed:
        if mapping is not None:
            item = conn.execute("SELECT * FROM items WHERE id = ?", (mapping["item_id"],)).fetchone()
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
        _set_mapping(conn, cur.lastrowid, provider, remote, remote_hash)
        stats["pulled"] += 1
        return

    if remote_hash == mapping["content_hash"]:  # overlap-window echo or our own push — no-op
        conn.execute("UPDATE item_remote SET remote_updated_at = ? WHERE id = ?", (remote.updated_at, mapping["id"]))
        return

    item = conn.execute("SELECT * FROM items WHERE id = ?", (mapping["item_id"],)).fetchone()
    local_changed = content_hash(item["title"], item["body"]) != mapping["content_hash"]
    if local_changed and parse_ts(remote.updated_at) < parse_ts(item["updated_at"]):
        # local wins; remote version (the loser) goes to history, push phase overwrites remote
        record_history(conn, item["id"], remote.title, remote.body_md, remote.updated_at, "conflict-local-won")
        return
    if local_changed:  # remote newer or same-minute tie -> remote wins
        record_history(conn, item["id"], item["title"], item["body"], item["updated_at"], "conflict-remote-won")
    update_item(conn, item["id"], remote.title, remote.body_md, remote.updated_at)
    conn.execute("UPDATE item_remote SET content_hash = ?, remote_updated_at = ? WHERE id = ?", (remote_hash, remote.updated_at, mapping["id"]))
    stats["pulled"] += 1


def _push_local(conn: sqlite3.Connection, client: SyncClient, space_id: int, target_id: str, stats: dict) -> None:
    items = conn.execute("SELECT * FROM items WHERE space_id = ?", (space_id,)).fetchall()
    for item in items:
        mapping = conn.execute("SELECT * FROM item_remote WHERE item_id = ? AND provider = ?", (item["id"], client.provider)).fetchone()
        if item["archived"]:
            if mapping is not None:
                call_with_retry(client.delete, mapping["remote_id"])
                conn.execute("DELETE FROM item_remote WHERE id = ?", (mapping["id"],))
                stats["deleted"] += 1
            continue
        local_hash = content_hash(item["title"], item["body"])
        if mapping is None:
            remote = call_with_retry(client.create, target_id, item["title"], item["body"])
            _set_mapping(conn, item["id"], client.provider, remote, local_hash)
            stats["pushed"] += 1
        elif local_hash != mapping["content_hash"]:
            remote = call_with_retry(client.update, mapping["remote_id"], item["title"], item["body"])
            _set_mapping(conn, item["id"], client.provider, remote, local_hash)
            stats["pushed"] += 1

"""Repository layer. Every item read/write goes through here, filtered by --user membership.

RBAC is soft: --user is trusted, not authenticated. Visibility = spaces the user is a member of.
"""

import hashlib
import shutil
import sqlite3
from pathlib import Path

from brain.db import iso_now


def content_hash(title: str, body: str) -> str:
    return hashlib.sha256(f"{title}\0{body}".encode()).hexdigest()


def seed(conn: sqlite3.Connection, config: dict) -> None:
    """Idempotently create users from config.toml, personal:<user> spaces, and shared.

    `users` may be a legacy list of names or a table of name -> {telegram: ...};
    iterating either yields names, so the loop below handles both.
    """
    users = config.get("users", [])
    conn.execute("INSERT OR IGNORE INTO spaces (name) VALUES ('shared')")
    for name in users:
        conn.execute("INSERT OR IGNORE INTO users (name) VALUES (?)", (name,))
        conn.execute("INSERT OR IGNORE INTO spaces (name) VALUES (?)", (f"personal:{name}",))
        conn.execute(
            "INSERT OR IGNORE INTO space_members (space_id, user_id) SELECT s.id, u.id FROM spaces s, users u WHERE s.name = ? AND u.name = ?",
            (f"personal:{name}", name),
        )
        conn.execute(
            "INSERT OR IGNORE INTO space_members (space_id, user_id) SELECT s.id, u.id FROM spaces s, users u WHERE s.name = 'shared' AND u.name = ?",
            (name,),
        )
    conn.commit()


def user_id(conn: sqlite3.Connection, name: str) -> int:
    row = conn.execute("SELECT id FROM users WHERE name = ?", (name,)).fetchone()
    if row is None:
        raise SystemExit(f"unknown user: {name} (add to config.toml and run `brain migrate`)")
    return row["id"]


def space_id(conn: sqlite3.Connection, name: str) -> int:
    row = conn.execute("SELECT id FROM spaces WHERE name = ?", (name,)).fetchone()
    if row is None:
        raise SystemExit(f"unknown space: {name}")
    return row["id"]


def visible_space_ids(conn: sqlite3.Connection, user: str) -> list[int]:
    uid = user_id(conn, user)
    return [r["id"] for r in conn.execute("SELECT s.id FROM spaces s JOIN space_members m ON m.space_id = s.id WHERE m.user_id = ?", (uid,))]


def require_visible(conn: sqlite3.Connection, user: str, sid: int) -> None:
    if sid not in visible_space_ids(conn, user):
        raise SystemExit(f"user {user} is not a member of that space")


def add_space(conn: sqlite3.Connection, name: str, members: list[str] | None = None) -> int:
    conn.execute("INSERT INTO spaces (name) VALUES (?)", (name,))
    sid = space_id(conn, name)
    if members is None:  # default: every configured user
        members = [r["name"] for r in conn.execute("SELECT name FROM users")]
    for member in members:
        conn.execute("INSERT OR IGNORE INTO space_members (space_id, user_id) VALUES (?, ?)", (sid, user_id(conn, member)))
    conn.commit()
    return sid


def list_spaces(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT s.id, s.name, group_concat(u.name) AS members FROM spaces s "
        "LEFT JOIN space_members m ON m.space_id = s.id LEFT JOIN users u ON u.id = m.user_id GROUP BY s.id ORDER BY s.name"
    )
    return [{"id": r["id"], "name": r["name"], "members": (r["members"] or "").split(",") if r["members"] else []} for r in rows]


def set_target(conn: sqlite3.Connection, space: str, provider: str, remote_id: str) -> None:
    sid = space_id(conn, space)
    conn.execute(
        "INSERT INTO sync_targets (space_id, provider, remote_id) VALUES (?, ?, ?) "
        "ON CONFLICT (space_id, provider) DO UPDATE SET remote_id = excluded.remote_id, cursor = NULL",
        (sid, provider, remote_id),
    )
    conn.commit()


def add_item(
    conn: sqlite3.Connection,
    user: str,
    space: str | None = None,
    title: str = "",
    body: str = "",
    source: str = "",
    kind: str = "note",
) -> int:
    space = space or f"personal:{user}"
    sid = space_id(conn, space)
    require_visible(conn, user, sid)
    if not title:
        title = body.strip().splitlines()[0][:80] if body.strip() else "(untitled)"
    now = iso_now()
    cur = conn.execute(
        "INSERT INTO items (space_id, title, body, source, kind, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (sid, title, body, source, kind, now, now),
    )
    conn.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


def get_item(conn: sqlite3.Connection, user: str, item_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        raise SystemExit(f"no such item: {item_id}")
    require_visible(conn, user, row["space_id"])
    return row


def list_items(conn: sqlite3.Connection, user: str, space: str | None = None, include_archived: bool = False) -> list[sqlite3.Row]:
    sids = visible_space_ids(conn, user)
    if space is not None:
        sid = space_id(conn, space)
        require_visible(conn, user, sid)
        sids = [sid]
    if not sids:
        return []
    placeholders = ",".join("?" * len(sids))
    query = f"SELECT * FROM items WHERE space_id IN ({placeholders})"
    if not include_archived:
        query += " AND archived = 0"
    return list(conn.execute(query + " ORDER BY updated_at DESC", sids))


def record_history(conn: sqlite3.Connection, item_id: int, title: str, body: str, updated_at: str, reason: str) -> None:
    conn.execute(
        "INSERT INTO item_history (item_id, title, body, updated_at, reason, recorded_at) VALUES (?, ?, ?, ?, ?, ?)",
        (item_id, title, body, updated_at, reason, iso_now()),
    )


def update_item(conn: sqlite3.Connection, item_id: int, title: str, body: str, updated_at: str | None = None) -> None:
    conn.execute("UPDATE items SET title = ?, body = ?, updated_at = ? WHERE id = ?", (title, body, updated_at or iso_now(), item_id))


def edit_item(conn: sqlite3.Connection, user: str, item_id: int, title: str | None = None, body: str | None = None) -> None:
    row = get_item(conn, user, item_id)
    record_history(conn, item_id, row["title"], row["body"], row["updated_at"], "edited")
    update_item(conn, item_id, title if title is not None else row["title"], body if body is not None else row["body"])
    conn.commit()


def move_item(conn: sqlite3.Connection, user: str, item_id: int, space: str) -> None:
    row = get_item(conn, user, item_id)
    sid = space_id(conn, space)
    require_visible(conn, user, sid)
    record_history(conn, item_id, row["title"], row["body"], row["updated_at"], "moved")
    conn.execute("UPDATE items SET space_id = ?, updated_at = ? WHERE id = ?", (sid, iso_now(), item_id))
    conn.commit()


def archive_item(conn: sqlite3.Connection, user: str, item_id: int, reason: str = "archived") -> None:
    row = get_item(conn, user, item_id)
    record_history(conn, item_id, row["title"], row["body"], row["updated_at"], reason)
    conn.execute("UPDATE items SET archived = 1, updated_at = ? WHERE id = ?", (iso_now(), item_id))
    conn.commit()


def list_users(conn: sqlite3.Connection, config: dict) -> list[dict]:
    users_cfg = config.get("users", [])
    telegram_by_name = users_cfg if isinstance(users_cfg, dict) else {}
    return [
        {"name": r["name"], "telegram": telegram_by_name.get(r["name"], {}).get("telegram", "")}
        for r in conn.execute("SELECT name FROM users ORDER BY name")
    ]


def add_attachment(conn: sqlite3.Connection, user: str, item_id: int, path: Path, files_root: Path) -> str:
    get_item(conn, user, item_id)  # visibility check
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    dest_dir = files_root / str(item_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, dest_dir / path.name)
    conn.execute("INSERT INTO attachments (item_id, filename, sha256) VALUES (?, ?, ?)", (item_id, path.name, digest))
    conn.commit()
    return digest

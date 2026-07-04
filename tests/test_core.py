import pytest

from brain import db, repo


def test_migrate_is_idempotent(conn):
    assert db.migrate(conn) == []  # already applied by fixture
    repo.seed(conn, {"users": ["alice", "bob"]})  # reseeding is a no-op
    assert {s["name"] for s in repo.list_spaces(conn)} == {"personal:alice", "personal:bob", "shared"}


def test_personal_spaces_are_isolated(conn):
    secret = repo.add_item(conn, "alice", title="alice secret", body="the password is xyzzy")
    shared = repo.add_item(conn, "alice", space="shared", title="shared note", body="both can see")

    bob_items = {r["id"] for r in repo.list_items(conn, "bob")}
    assert secret not in bob_items
    assert shared in bob_items

    with pytest.raises(SystemExit):
        repo.get_item(conn, "bob", secret)
    with pytest.raises(SystemExit):
        repo.add_item(conn, "bob", space="personal:alice", title="intrusion")


def test_custom_space_membership(conn):
    repo.add_space(conn, "project-x", members=["alice"])
    item = repo.add_item(conn, "alice", space="project-x", title="plan")
    assert item not in {r["id"] for r in repo.list_items(conn, "bob")}
    with pytest.raises(SystemExit):
        repo.list_items(conn, "bob", space="project-x")


def test_archive_records_history(conn):
    item = repo.add_item(conn, "alice", title="temp", body="scratch")
    repo.archive_item(conn, "alice", item)
    assert item not in {r["id"] for r in repo.list_items(conn, "alice")}
    assert item in {r["id"] for r in repo.list_items(conn, "alice", include_archived=True)}
    history = conn.execute("SELECT * FROM item_history WHERE item_id = ?", (item,)).fetchall()
    assert len(history) == 1 and history[0]["body"] == "scratch"


def test_attachments(conn, tmp_path):
    item = repo.add_item(conn, "alice", title="with file")
    source = tmp_path / "photo.png"
    source.write_bytes(b"fake image bytes")
    files_root = tmp_path / "files"
    digest = repo.add_attachment(conn, "alice", item, source, files_root)
    assert (files_root / str(item) / "photo.png").read_bytes() == b"fake image bytes"
    row = conn.execute("SELECT * FROM attachments WHERE item_id = ?", (item,)).fetchone()
    assert row["filename"] == "photo.png" and row["sha256"] == digest


def test_untitled_item_takes_title_from_body(conn):
    item = repo.add_item(conn, "alice", body="first line becomes title\nrest of body")
    assert repo.get_item(conn, "alice", item)["title"] == "first line becomes title"


def test_seed_accepts_dict_form_users(conn):
    repo.seed(conn, {"users": {"alice": {"telegram": "111"}, "bob": {"telegram": "222"}}})
    assert {s["name"] for s in repo.list_spaces(conn)} == {"personal:alice", "personal:bob", "shared"}


def test_edit_item_records_history_and_updates_body(conn):
    item = repo.add_item(conn, "alice", title="orig", body="orig body")
    before = repo.get_item(conn, "alice", item)["updated_at"]
    repo.edit_item(conn, "alice", item, body="new body")
    row = repo.get_item(conn, "alice", item)
    assert row["title"] == "orig" and row["body"] == "new body"
    assert row["updated_at"] >= before
    history = conn.execute("SELECT * FROM item_history WHERE item_id = ?", (item,)).fetchall()
    assert len(history) == 1 and history[0]["body"] == "orig body" and history[0]["reason"] == "edited"
    hits = conn.execute("SELECT rowid FROM items_fts WHERE items_fts MATCH 'new'").fetchall()
    assert item in {r["rowid"] for r in hits}


def test_edit_item_by_non_member_raises(conn):
    item = repo.add_item(conn, "alice", title="secret")
    with pytest.raises(SystemExit):
        repo.edit_item(conn, "bob", item, body="hacked")


def test_move_item_changes_space_and_records_history(conn):
    item = repo.add_item(conn, "alice", space="shared", title="promote me")
    repo.move_item(conn, "alice", item, "personal:alice")
    row = repo.get_item(conn, "alice", item)
    assert row["space_id"] == repo.space_id(conn, "personal:alice")
    history = conn.execute("SELECT * FROM item_history WHERE item_id = ?", (item,)).fetchall()
    assert len(history) == 1 and history[0]["reason"] == "moved"


def test_move_item_to_space_bob_cannot_see_raises(conn):
    repo.add_space(conn, "project-x", members=["alice"])
    item = repo.add_item(conn, "alice", space="shared", title="plan")  # bob can see the item, not the destination
    with pytest.raises(SystemExit):
        repo.move_item(conn, "bob", item, "project-x")


def test_list_users(conn):
    repo.seed(conn, {"users": {"alice": {"telegram": "111"}, "bob": {"telegram": "222"}}})
    assert repo.list_users(conn, {"users": {"alice": {"telegram": "111"}, "bob": {"telegram": "222"}}}) == [
        {"name": "alice", "telegram": "111"},
        {"name": "bob", "telegram": "222"},
    ]
    assert repo.list_users(conn, {"users": ["alice", "bob"]}) == [
        {"name": "alice", "telegram": ""},
        {"name": "bob", "telegram": ""},
    ]

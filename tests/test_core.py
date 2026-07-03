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

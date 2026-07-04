import pytest

from brain.db import brain_dir
from brain import repo
from brain.sync import RateLimited, RemoteItem, call_with_retry, sync_provider

T0 = "2026-07-03T12:00:00+00:00"
T1 = "2026-07-03T13:00:00+00:00"
T2 = "2026-07-03T14:00:00+00:00"
T3 = "2026-07-03T15:00:00+00:00"


class FakeRemote:
    """In-memory stand-in for Notion/Drive: items per target, write-call counters."""

    provider = "notion"

    def __init__(self) -> None:
        self.targets: dict[str, dict[str, RemoteItem]] = {}
        self.creates = self.updates = self.deletes = 0
        self.uploads = 0
        self.uploaded_files: list[tuple[str, str, bytes, str]] = []
        self.now = T0
        self.fail_next: int = 0

    # -- test helpers ------------------------------------------------------
    def seed(self, target_id: str, remote_id: str, title: str, body: str, updated_at: str, read_only: bool = False) -> None:
        self.targets.setdefault(target_id, {})[remote_id] = RemoteItem(remote_id, title, body, updated_at, read_only=read_only)

    def edit(self, remote_id: str, body: str, updated_at: str) -> None:
        item = self._find(remote_id)
        item.body_md, item.updated_at = body, updated_at

    def trash(self, remote_id: str, updated_at: str) -> None:
        item = self._find(remote_id)
        item.trashed, item.updated_at = True, updated_at

    def vanish(self, remote_id: str) -> None:
        """Drop an item with no change event — how Notion's query hides trashed/moved-out pages."""
        for items in self.targets.values():
            items.pop(remote_id, None)

    def _find(self, remote_id: str) -> RemoteItem:
        for items in self.targets.values():
            if remote_id in items:
                return items[remote_id]
        raise KeyError(remote_id)

    def _maybe_fail(self) -> None:
        if self.fail_next:
            self.fail_next -= 1
            raise RateLimited(0)

    # -- SyncClient protocol -----------------------------------------------
    def list_changes(self, target_id: str, cursor: str | None) -> tuple[list[RemoteItem], str, list[str]]:
        self._maybe_fail()
        items = [i for i in self.targets.get(target_id, {}).values() if cursor is None or i.updated_at > cursor]
        timestamps = [i.updated_at for i in self.targets.get(target_id, {}).values()]
        present = [i.remote_id for i in self.targets.get(target_id, {}).values() if not i.trashed]
        return list(items), max([cursor or self.now, *timestamps]), present

    def fetch(self, remote_id: str) -> RemoteItem:
        self._maybe_fail()
        return self._find(remote_id)

    def create(self, target_id: str, title: str, body_md: str) -> RemoteItem:
        self._maybe_fail()
        self.creates += 1
        remote_id = f"r{self.creates}-{target_id}"
        item = RemoteItem(remote_id, title, body_md, self.now)
        self.targets.setdefault(target_id, {})[remote_id] = item
        return item

    def update(self, remote_id: str, title: str, body_md: str) -> RemoteItem:
        self._maybe_fail()
        self.updates += 1
        item = self._find(remote_id)
        item.title, item.body_md, item.updated_at = title, body_md, self.now
        return item

    def delete(self, remote_id: str) -> None:
        self._maybe_fail()
        self.deletes += 1
        self._find(remote_id).trashed = True

    def upload_file(self, target_id: str, filename: str, data: bytes) -> str:
        self._maybe_fail()
        self.uploads += 1
        remote_id = f"u{self.uploads}-{target_id}"
        self.uploaded_files.append((target_id, filename, data, remote_id))
        return remote_id


@pytest.fixture
def fake(conn):
    repo.set_target(conn, "personal:alice", "notion", "db-alice")
    return FakeRemote()


def test_initial_import_pulls_all_remote_content(conn, fake):
    fake.seed("db-alice", "legacy1", "old note", "from the legacy inbox", T0)
    fake.seed("db-alice", "legacy2", "another", "more legacy content", T0)
    stats = sync_provider(conn, fake)
    assert stats["pulled"] == 2
    titles = {r["title"] for r in repo.list_items(conn, "alice")}
    assert {"old note", "another"} <= titles


def test_local_items_push_to_remote(conn, fake):
    repo.add_item(conn, "alice", title="local note", body="hello remote")
    sync_provider(conn, fake)
    assert fake.creates == 1
    assert [i.body_md for i in fake.targets["db-alice"].values()] == ["hello remote"]


def test_no_changes_makes_zero_write_calls(conn, fake):
    fake.seed("db-alice", "n1", "remote note", "body", T0)
    repo.add_item(conn, "alice", title="local note", body="hello")
    sync_provider(conn, fake)  # reach steady state
    writes_before = (fake.creates, fake.updates, fake.deletes, fake.uploads)
    sync_provider(conn, fake)
    sync_provider(conn, fake)
    assert (fake.creates, fake.updates, fake.deletes, fake.uploads) == writes_before


def test_conflict_remote_newer_remote_wins_loser_in_history(conn, fake):
    item = repo.add_item(conn, "alice", title="note", body="original")
    sync_provider(conn, fake)
    repo.update_item(conn, item, "note", "local edit", updated_at=T1)
    remote_id = next(iter(fake.targets["db-alice"]))
    fake.edit(remote_id, "remote edit", updated_at=T2)
    sync_provider(conn, fake)
    assert repo.get_item(conn, "alice", item)["body"] == "remote edit"
    history = [r["body"] for r in conn.execute("SELECT body FROM item_history WHERE item_id = ? AND reason = 'conflict-remote-won'", (item,))]
    assert history == ["local edit"]


def test_conflict_same_minute_tie_remote_wins(conn, fake):
    item = repo.add_item(conn, "alice", title="note", body="original")
    sync_provider(conn, fake)
    repo.update_item(conn, item, "note", "local edit", updated_at=T2)
    fake.edit(next(iter(fake.targets["db-alice"])), "remote edit", updated_at=T2)
    sync_provider(conn, fake)
    assert repo.get_item(conn, "alice", item)["body"] == "remote edit"


def test_conflict_local_newer_local_wins_remote_version_in_history(conn, fake):
    item = repo.add_item(conn, "alice", title="note", body="original")
    sync_provider(conn, fake)
    remote_id = next(iter(fake.targets["db-alice"]))
    fake.edit(remote_id, "remote edit", updated_at=T1)
    repo.update_item(conn, item, "note", "local edit", updated_at=T2)
    fake.now = T3
    sync_provider(conn, fake)
    assert repo.get_item(conn, "alice", item)["body"] == "local edit"
    assert fake.targets["db-alice"][remote_id].body_md == "local edit"
    history = [r["body"] for r in conn.execute("SELECT body FROM item_history WHERE item_id = ? AND reason = 'conflict-local-won'", (item,))]
    assert history == ["remote edit"]


def test_remote_delete_archives_locally_never_hard_deletes(conn, fake):
    fake.seed("db-alice", "n1", "doomed", "body", T0)
    sync_provider(conn, fake)
    fake.trash("n1", updated_at=T1)
    stats = sync_provider(conn, fake)
    assert stats["archived"] == 1
    items = repo.list_items(conn, "alice", include_archived=True)
    assert [r["archived"] for r in items if r["title"] == "doomed"] == [1]
    reasons = {r["reason"] for r in conn.execute("SELECT reason FROM item_history")}
    assert "remote-delete" in reasons


def test_local_archive_propagates_as_remote_delete(conn, fake):
    item = repo.add_item(conn, "alice", title="temp", body="scratch")
    sync_provider(conn, fake)
    repo.archive_item(conn, "alice", item)
    sync_provider(conn, fake)
    assert fake.deletes == 1
    assert all(i.trashed for i in fake.targets["db-alice"].values())


def test_read_only_remote_item_never_pushes_or_deletes(conn, fake):
    fake.seed("db-alice", "gdoc-1", "doc", "remote body", T0, read_only=True)
    sync_provider(conn, fake)
    item = repo.list_items(conn, "alice")[0]["id"]

    repo.update_item(conn, item, "doc", "local edit", updated_at=T1)
    sync_provider(conn, fake)
    assert fake.updates == 0

    repo.archive_item(conn, "alice", item)
    sync_provider(conn, fake)
    assert fake.deletes == 0


def test_moved_item_rehomes_between_targets_in_one_sync(conn, fake):
    repo.set_target(conn, "shared", "notion", "db-shared")
    item = repo.add_item(conn, "alice", title="move me", body="shared now")
    sync_provider(conn, fake)
    creates_before = fake.creates
    deletes_before = fake.deletes
    shared_space_id = conn.execute("SELECT id FROM spaces WHERE name = ?", ("shared",)).fetchone()["id"]

    conn.execute("UPDATE items SET space_id = ? WHERE id = ?", (shared_space_id, item))
    stats = sync_provider(conn, fake)

    assert fake.deletes - deletes_before == 1
    assert fake.creates - creates_before == 1
    assert stats["deleted"] == 1
    assert all(i.trashed for i in fake.targets["db-alice"].values() if i.title == "move me")
    assert {i.title for i in fake.targets["db-shared"].values() if not i.trashed} == {"move me"}
    mapping = conn.execute("SELECT space_id FROM item_remote WHERE item_id = ? AND provider = ?", (item, fake.provider)).fetchone()
    assert mapping["space_id"] == shared_space_id


def test_attachments_upload_once_to_remote_with_optional_capability(conn, fake):
    item = repo.add_item(conn, "alice", title="with file", body="body")
    filename = "note.bin"
    data = b"attachment bytes"
    file_dir = brain_dir() / "files" / str(item)
    file_dir.mkdir(parents=True)
    (file_dir / filename).write_bytes(data)
    conn.execute("INSERT INTO attachments (item_id, filename, sha256) VALUES (?, ?, ?)", (item, filename, "dummy"))

    sync_provider(conn, fake)

    assert fake.uploads == 1
    assert fake.uploaded_files == [("db-alice", f"{item}-{filename}", data, "u1-db-alice")]
    assert conn.execute("SELECT drive_file_id FROM attachments WHERE item_id = ?", (item,)).fetchone()["drive_file_id"] == "u1-db-alice"

    sync_provider(conn, fake)
    assert fake.uploads == 1


def test_one_local_add_fans_out_to_both_notion_and_drive(conn, fake):
    """The headline requirement: an agent adds one row locally and it appears in the
    space's Notion database AND Drive folder; a later edit propagates to both too."""
    drive = FakeRemote()
    drive.provider = "drive"
    repo.set_target(conn, "personal:alice", "drive", "folder-alice")

    item = repo.add_item(conn, "alice", title="new doc", body="hello world")
    sync_provider(conn, fake)
    sync_provider(conn, drive)
    assert {i.title for i in fake.targets["db-alice"].values()} == {"new doc"}
    assert {i.title for i in drive.targets["folder-alice"].values()} == {"new doc"}

    repo.edit_item(conn, "alice", item, body="hello world, revised")
    sync_provider(conn, fake)
    sync_provider(conn, drive)
    assert [i.body_md for i in fake.targets["db-alice"].values()] == ["hello world, revised"]
    assert [i.body_md for i in drive.targets["folder-alice"].values()] == ["hello world, revised"]


def test_remote_notion_edit_relays_to_drive_via_db(conn, fake):
    """Hub-and-spoke: an edit made in Notion lands locally, then the next Drive sync
    pushes it out — the DB is the hub between the two remotes."""
    drive = FakeRemote()
    drive.provider = "drive"
    repo.set_target(conn, "personal:alice", "drive", "folder-alice")
    repo.add_item(conn, "alice", title="doc", body="v1")
    sync_provider(conn, fake)
    sync_provider(conn, drive)

    fake.edit(next(iter(fake.targets["db-alice"])), "v2 from notion", updated_at=T1)
    sync_provider(conn, fake)
    sync_provider(conn, drive)
    assert [i.body_md for i in drive.targets["folder-alice"].values()] == ["v2 from notion"]


def test_personal_items_never_reach_other_spaces_target(conn, fake):
    repo.set_target(conn, "shared", "notion", "db-shared")
    repo.add_item(conn, "alice", title="private", body="alice only")
    repo.add_item(conn, "alice", space="shared", title="public", body="for everyone")
    sync_provider(conn, fake)
    shared_titles = {i.title for i in fake.targets.get("db-shared", {}).values()}
    assert shared_titles == {"public"}
    alice_titles = {i.title for i in fake.targets.get("db-alice", {}).values()}
    assert alice_titles == {"private"}


def test_silently_vanished_remote_page_archives_locally(conn, fake):
    """Notion's query omits trashed/moved-out pages with no change event — absence from the
    full listing must archive the local item."""
    fake.seed("db-alice", "n1", "quietly removed", "body", T0)
    sync_provider(conn, fake)
    fake.vanish("n1")
    stats = sync_provider(conn, fake)
    assert stats["archived"] == 1
    items = repo.list_items(conn, "alice", include_archived=True)
    assert [r["archived"] for r in items if r["title"] == "quietly removed"] == [1]


def test_page_moved_in_with_old_timestamp_still_syncs(conn, fake):
    """A page moved into the database keeps its old last_edited_time (before the cursor) —
    it must be fetched via the presence listing, not missed."""
    repo.add_item(conn, "alice", title="existing", body="x")
    sync_provider(conn, fake)  # establishes a cursor at T0
    fake.seed("db-alice", "old-page", "moved in", "content from long ago", "2026-07-03T01:00:00+00:00")
    stats = sync_provider(conn, fake)
    assert stats["pulled"] == 1
    assert "moved in" in {r["title"] for r in repo.list_items(conn, "alice")}


def test_sync_retries_on_rate_limit(conn, fake):
    repo.add_item(conn, "alice", title="note", body="body")
    fake.fail_next = 2  # list_changes 429s twice, then succeeds
    sync_provider(conn, fake)
    assert fake.creates == 1


def test_call_with_retry_gives_up_after_max_attempts():
    calls = {"n": 0}

    def always_limited():
        calls["n"] += 1
        raise RateLimited(0.5)

    with pytest.raises(RateLimited):
        call_with_retry(always_limited, attempts=3, sleep=lambda s: None)
    assert calls["n"] == 3

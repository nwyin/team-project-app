import pytest

from brain import db, repo


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("BRAIN_DIR", str(tmp_path))
    connection = db.connect(tmp_path)
    db.migrate(connection)
    repo.seed(connection, {"users": ["alice", "bob"]})
    yield connection
    connection.close()

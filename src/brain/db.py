"""Connection, config, and migrations for the brain SQLite DB.

All state lives under BRAIN_DIR (default: cwd): brain.db, config.toml, files/.
"""

import os
import sqlite3
import tomllib
from datetime import UTC, datetime
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def brain_dir() -> Path:
    return Path(os.environ.get("BRAIN_DIR", "."))


def load_config(directory: Path | None = None) -> dict:
    path = (directory or brain_dir()) / "config.toml"
    if not path.exists():
        return {}
    return tomllib.loads(path.read_text())


def connect(directory: Path | None = None) -> sqlite3.Connection:
    d = directory or brain_dir()
    d.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(d / "brain.db")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def migrate(conn: sqlite3.Connection) -> list[str]:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY)")
    applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
    ran = []
    for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if sql_file.name in applied:
            continue
        conn.executescript(sql_file.read_text())
        conn.execute("INSERT INTO schema_migrations (version) VALUES (?)", (sql_file.name,))
        ran.append(sql_file.name)
    conn.commit()
    return ran


def iso_now() -> str:
    return datetime.now(UTC).isoformat()


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))

CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE spaces (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE space_members (
    space_id INTEGER NOT NULL REFERENCES spaces(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    UNIQUE (space_id, user_id)
);

CREATE TABLE items (
    id INTEGER PRIMARY KEY,
    space_id INTEGER NOT NULL REFERENCES spaces(id),
    title TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'note',
    archived INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE item_history (
    id INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL REFERENCES items(id),
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    reason TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);

CREATE TABLE attachments (
    id INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL REFERENCES items(id),
    filename TEXT NOT NULL,
    sha256 TEXT NOT NULL
);

-- Per-space sync targets: a Notion database and/or a Drive folder. Cursor is
-- provider-specific (Notion: ISO timestamp; Drive: changes page token).
CREATE TABLE sync_targets (
    id INTEGER PRIMARY KEY,
    space_id INTEGER NOT NULL REFERENCES spaces(id),
    provider TEXT NOT NULL CHECK (provider IN ('notion', 'drive')),
    remote_id TEXT NOT NULL,
    cursor TEXT,
    UNIQUE (space_id, provider)
);

-- Local item <-> remote object mapping. content_hash is the hash at last sync;
-- differing local hash means "push", differing remote hash means "pull".
CREATE TABLE item_remote (
    id INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL REFERENCES items(id),
    provider TEXT NOT NULL,
    remote_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    remote_updated_at TEXT,
    UNIQUE (provider, remote_id),
    UNIQUE (item_id, provider)
);

CREATE TABLE embeddings (
    item_id INTEGER PRIMARY KEY REFERENCES items(id),
    vector BLOB NOT NULL,
    content_hash TEXT NOT NULL
);

CREATE VIRTUAL TABLE items_fts USING fts5(title, body, content='items', content_rowid='id');

CREATE TRIGGER items_ai AFTER INSERT ON items BEGIN
    INSERT INTO items_fts(rowid, title, body) VALUES (new.id, new.title, new.body);
END;

CREATE TRIGGER items_ad AFTER DELETE ON items BEGIN
    INSERT INTO items_fts(items_fts, rowid, title, body) VALUES ('delete', old.id, old.title, old.body);
END;

CREATE TRIGGER items_au AFTER UPDATE OF title, body ON items BEGIN
    INSERT INTO items_fts(items_fts, rowid, title, body) VALUES ('delete', old.id, old.title, old.body);
    INSERT INTO items_fts(rowid, title, body) VALUES (new.id, new.title, new.body);
END;

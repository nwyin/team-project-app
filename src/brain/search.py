"""Indexing (embeddings via litellm) and hybrid FTS5 + vector search."""

import math
import re
import sqlite3
import struct
from collections.abc import Callable

from brain.repo import content_hash, visible_space_ids

Embedder = Callable[[list[str]], list[list[float]]]


def litellm_embedder(config: dict) -> Embedder:
    emb_cfg = config.get("embeddings", {})
    model = emb_cfg.get("model")
    if not model:
        raise SystemExit("no [embeddings] model in config.toml")

    def embed(texts: list[str]) -> list[list[float]]:
        import litellm  # ponytail: lazy import, litellm costs seconds at import time

        response = litellm.embedding(model=model, input=texts, api_base=emb_cfg.get("api_base") or None)
        return [d["embedding"] for d in response["data"]]

    return embed


def pack(vector: list[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


def unpack(blob: bytes) -> list[float]:
    return list(struct.unpack(f"{len(blob) // 4}f", blob))


def index_items(conn: sqlite3.Connection, embedder: Embedder) -> int:
    """Embed items whose content changed since last index. Returns count embedded."""
    rows = conn.execute(
        "SELECT i.id, i.title, i.body FROM items i LEFT JOIN embeddings e ON e.item_id = i.id "
        "WHERE i.archived = 0 AND (e.item_id IS NULL OR e.content_hash != ?)",
        ("",),
    ).fetchall()
    # hash check needs python (hash of current title/body), so filter here
    pending = []
    for row in rows:
        current = content_hash(row["title"], row["body"])
        stored = conn.execute("SELECT content_hash FROM embeddings WHERE item_id = ?", (row["id"],)).fetchone()
        if stored is None or stored["content_hash"] != current:
            pending.append((row["id"], f"{row['title']}\n{row['body']}", current))
    if not pending:
        return 0
    vectors = embedder([text for _, text, _ in pending])
    for (item_id, _, digest), vector in zip(pending, vectors):
        conn.execute(
            "INSERT INTO embeddings (item_id, vector, content_hash) VALUES (?, ?, ?) "
            "ON CONFLICT (item_id) DO UPDATE SET vector = excluded.vector, content_hash = excluded.content_hash",
            (item_id, pack(vector), digest),
        )
    conn.commit()
    return len(pending)


def _fts_query(query: str) -> str:
    tokens = re.findall(r"\w+", query)
    return " ".join(tokens)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def hybrid_search(
    conn: sqlite3.Connection,
    user: str,
    query: str,
    embedder: Embedder | None = None,
    limit: int = 10,
) -> list[dict]:
    """FTS5 + vector ranks fused with reciprocal rank fusion, RBAC-filtered."""
    sids = visible_space_ids(conn, user)
    if not sids:
        return []
    placeholders = ",".join("?" * len(sids))

    fts_ids: list[int] = []
    match = _fts_query(query)
    if match:
        fts_ids = [
            r["id"]
            for r in conn.execute(
                f"SELECT i.id FROM items_fts f JOIN items i ON i.id = f.rowid "
                f"WHERE items_fts MATCH ? AND i.archived = 0 AND i.space_id IN ({placeholders}) ORDER BY f.rank LIMIT 50",
                (match, *sids),
            )
        ]

    vec_ids: list[int] = []
    if embedder is not None:
        # ponytail: brute-force cosine over visible items; swap in sqlite-vec when scans get slow (>10k items)
        query_vec = embedder([query])[0]
        rows = conn.execute(
            f"SELECT e.item_id, e.vector FROM embeddings e JOIN items i ON i.id = e.item_id "
            f"WHERE i.archived = 0 AND i.space_id IN ({placeholders})",
            sids,
        ).fetchall()
        scored = [(_cosine(query_vec, unpack(r["vector"])), r["item_id"]) for r in rows]
        vec_ids = [item_id for score, item_id in sorted(scored, reverse=True)[:50] if score > 0]

    fused: dict[int, float] = {}
    for ranked in (fts_ids, vec_ids):
        for rank, item_id in enumerate(ranked):
            fused[item_id] = fused.get(item_id, 0.0) + 1.0 / (60 + rank)

    top = sorted(fused, key=lambda item_id: fused[item_id], reverse=True)[:limit]
    results = []
    for item_id in top:
        row = conn.execute("SELECT id, space_id, title, body, kind FROM items WHERE id = ?", (item_id,)).fetchone()
        space = conn.execute("SELECT name FROM spaces WHERE id = ?", (row["space_id"],)).fetchone()["name"]
        results.append({"id": row["id"], "space": space, "title": row["title"], "kind": row["kind"], "score": round(fused[item_id], 6)})
    return results

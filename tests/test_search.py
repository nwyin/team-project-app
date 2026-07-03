from brain import repo, search


def fake_embedder(texts: list[str]) -> list[list[float]]:
    """Deterministic 2-d embeddings: cat-adjacent text points one way, everything else the other."""
    return [[1.0, 0.1] if any(w in t.lower() for w in ("cat", "feline", "kitten")) else [0.1, 1.0] for t in texts]


def test_fts_finds_body_keyword(conn):
    repo.add_item(conn, "alice", title="groceries", body="buy tomatoes and basil")
    results = search.hybrid_search(conn, "alice", "basil")
    assert [r["title"] for r in results] == ["groceries"]


def test_hybrid_returns_semantically_related_item(conn):
    feline = repo.add_item(conn, "alice", title="pet notes", body="the feline sleeps all day")
    repo.add_item(conn, "alice", title="car notes", body="oil change due in march")
    assert search.index_items(conn, fake_embedder) == 2
    results = search.hybrid_search(conn, "alice", "cat", embedder=fake_embedder)
    assert results and results[0]["id"] == feline  # no FTS overlap: 'cat' is nowhere in the text


def test_index_skips_unchanged_items(conn):
    repo.add_item(conn, "alice", title="a", body="b")
    assert search.index_items(conn, fake_embedder) == 1
    assert search.index_items(conn, fake_embedder) == 0  # unchanged -> no re-embed


def test_search_respects_rbac(conn):
    repo.add_item(conn, "alice", title="secret cat plans", body="feline conspiracy")
    search.index_items(conn, fake_embedder)
    assert search.hybrid_search(conn, "bob", "feline conspiracy", embedder=fake_embedder) == []
    assert search.hybrid_search(conn, "alice", "feline conspiracy", embedder=fake_embedder) != []

"""Unit tests for Tier 2 context retriever."""

from __future__ import annotations

from pathlib import Path

import yaml

from harness.core.context_retriever import (
    ContextRetriever,
    ReferenceIndex,
)


def _build_domain(tmp_path: Path, chunks: list[dict]) -> Path:
    """Scaffold a minimal domain with reference_index.yaml + chunk files."""
    ctx = tmp_path / "context"
    (ctx / "reference" / "chunks").mkdir(parents=True)
    index = {"version": 1, "chunks": []}
    for entry in chunks:
        body = entry.pop("body", f"# {entry['id']}\n\nContent for {entry['id']}.\n")
        path = f"reference/chunks/{entry['id']}.md"
        (ctx / path).write_text(body)
        entry.setdefault("path", path)
        entry.setdefault("size_bytes", len(body))
        index["chunks"].append(entry)
    (ctx / "reference_index.yaml").write_text(yaml.safe_dump(index))
    return tmp_path


class TestReferenceIndexLoad:
    def test_missing_index_returns_empty(self, tmp_path):
        idx = ReferenceIndex.load(tmp_path)
        assert idx.chunks == []

    def test_parses_chunks(self, tmp_path):
        _build_domain(tmp_path, [
            {"id": "a", "topic": "Topic A", "keywords": ["x", "y"], "summary": "S"},
            {"id": "b", "topic": "Topic B", "keywords": ["z"], "summary": "T"},
        ])
        idx = ReferenceIndex.load(tmp_path)
        assert {c.id for c in idx.chunks} == {"a", "b"}
        chunk_a = next(c for c in idx.chunks if c.id == "a")
        assert chunk_a.keywords == ("x", "y")
        assert chunk_a.topic == "Topic A"

    def test_comma_string_keywords_parsed(self, tmp_path):
        _build_domain(tmp_path, [
            {"id": "a", "topic": "T", "keywords": "x, y, z", "summary": ""},
        ])
        idx = ReferenceIndex.load(tmp_path)
        assert idx.chunks[0].keywords == ("x", "y", "z")

    def test_malformed_yaml_graceful(self, tmp_path):
        ctx = tmp_path / "context"
        ctx.mkdir()
        (ctx / "reference_index.yaml").write_text("not: [valid: yaml: garbage]")
        idx = ReferenceIndex.load(tmp_path)
        assert idx.chunks == []


class TestScoring:
    def test_topic_substring_scores_high(self, tmp_path):
        _build_domain(tmp_path, [
            {"id": "routers", "topic": "FastAPI router patterns", "keywords": [], "summary": ""},
            {"id": "db", "topic": "SQLAlchemy models", "keywords": [], "summary": ""},
        ])
        r = ContextRetriever.for_domain(tmp_path)
        result = r.search(topic="router", top_k=5, min_score=0.0)
        assert result.chunks[0][0].id == "routers"
        assert result.chunks[0][1] >= 2.0  # substring match hits the +2.0 branch

    def test_keyword_overlap_scores(self, tmp_path):
        _build_domain(tmp_path, [
            {"id": "a", "topic": "Generic", "keywords": ["async", "await"], "summary": ""},
            {"id": "b", "topic": "Generic", "keywords": ["database"], "summary": ""},
        ])
        r = ContextRetriever.for_domain(tmp_path)
        result = r.search(topic="", keywords=["async"], top_k=5, min_score=0.0)
        assert result.chunks[0][0].id == "a"

    def test_inverse_rarity_weight(self, tmp_path):
        # Rare keyword 'xyz' in only chunk b → higher score per match vs
        # 'common' which is in 5 chunks.
        chunks = [
            {"id": f"c{i}", "topic": "t", "keywords": ["common"], "summary": ""}
            for i in range(5)
        ]
        chunks.append({"id": "b", "topic": "t", "keywords": ["xyz", "common"], "summary": ""})
        _build_domain(tmp_path, chunks)
        r = ContextRetriever.for_domain(tmp_path)
        result = r.search(topic="", keywords=["xyz", "common"], top_k=1, min_score=0.0)
        # 'b' should win — it has the rare kw 'xyz' + common
        assert result.chunks[0][0].id == "b"

    def test_no_match_returns_empty(self, tmp_path):
        _build_domain(tmp_path, [{"id": "a", "topic": "x", "keywords": ["p"], "summary": "q"}])
        r = ContextRetriever.for_domain(tmp_path)
        result = r.search(topic="completely unrelated", keywords=["z"], min_score=2.0)
        assert result.chunks == []

    def test_top_k_limits_results(self, tmp_path):
        chunks = [{"id": f"c{i}", "topic": "router patterns", "keywords": ["x"], "summary": ""} for i in range(10)]
        _build_domain(tmp_path, chunks)
        r = ContextRetriever.for_domain(tmp_path)
        result = r.search(topic="router", top_k=3, min_score=0.0)
        assert len(result.chunks) == 3


class TestChunkBodyResolution:
    def test_resolves_chunk_body(self, tmp_path):
        _build_domain(tmp_path, [
            {"id": "a", "topic": "T", "keywords": ["x"], "summary": "s",
             "body": "# A\n\nHello world."},
        ])
        idx = ReferenceIndex.load(tmp_path)
        body = idx.resolve_chunk_body(idx.chunks[0])
        assert "Hello world" in body

    def test_missing_chunk_returns_error_stub(self, tmp_path):
        _build_domain(tmp_path, [
            {"id": "gone", "topic": "T", "keywords": [], "summary": ""},
        ])
        (tmp_path / "context" / "reference" / "chunks" / "gone.md").unlink()
        idx = ReferenceIndex.load(tmp_path)
        body = idx.resolve_chunk_body(idx.chunks[0])
        assert "error" in body.lower()


class TestFormatForLlm:
    def test_matches_render_with_topic_and_score(self, tmp_path):
        _build_domain(tmp_path, [
            {"id": "a", "topic": "FastAPI routers", "keywords": ["router"], "summary": ""},
        ])
        r = ContextRetriever.for_domain(tmp_path)
        result = r.search(topic="router", top_k=3, min_score=0.0)
        rendered = result.format_for_llm(r)
        assert "context_retrieve" in rendered
        assert "FastAPI routers" in rendered
        assert "Chunk: `a`" in rendered

    def test_no_match_renders_fallback_hint(self, tmp_path):
        _build_domain(tmp_path, [{"id": "a", "topic": "x", "keywords": [], "summary": ""}])
        r = ContextRetriever.for_domain(tmp_path)
        result = r.search(topic="unrelated", min_score=5.0)
        rendered = result.format_for_llm(r)
        assert "No reference chunks matched" in rendered
        assert "origin_fetch" in rendered  # points at Tier 3 fallback

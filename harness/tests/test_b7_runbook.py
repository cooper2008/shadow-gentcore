"""Tests for B7 — runbook convention + RAG loader (fix/B7-runbook-convention).

The runbook convention: markdown files under `context/runbooks/*.md` with
optional YAML frontmatter carrying structured trigger/metadata info.

The loader:
- Parses frontmatter + body from each .md file
- Indexes by id + triggers
- Provides search by triggers or free-text query
- Returns structured hits with source_path + excerpts
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest


RUNBOOK_SCHEMA_DOC = (
    Path(__file__).resolve().parent.parent.parent / "config" / "runbook_schema.md"
)


def _write_runbook(dir_path: Path, filename: str, content: str) -> Path:
    path = dir_path / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


class TestRunbookSchemaDoc:
    def test_schema_doc_exists(self) -> None:
        assert RUNBOOK_SCHEMA_DOC.is_file(), (
            f"Expected runbook schema doc at {RUNBOOK_SCHEMA_DOC}"
        )

    def test_schema_doc_explains_frontmatter_fields(self) -> None:
        content = RUNBOOK_SCHEMA_DOC.read_text(encoding="utf-8")
        for field in ["id", "triggers", "estimated_duration", "blast_radius", "approval_required"]:
            assert field in content, (
                f"Runbook schema doc must mention frontmatter field `{field}`"
            )

    def test_schema_doc_shows_example(self) -> None:
        content = RUNBOOK_SCHEMA_DOC.read_text(encoding="utf-8")
        assert "---" in content, "Schema doc must include a YAML-frontmatter example"
        assert "triggers:" in content


class TestParseRunbook:
    def test_parses_frontmatter_and_body(self, tmp_path: Path) -> None:
        from harness.core.runbook_loader import parse_runbook

        rb = _write_runbook(
            tmp_path,
            "rds_failover.md",
            dedent("""\
            ---
            id: rds-multi-az-failover
            triggers: [rds_instance_unhealthy, rds_failover_required]
            estimated_duration: 15m
            blast_radius: single_db
            approval_required: true
            ---
            # RDS Multi-AZ Failover Runbook

            1. Verify replica lag
            2. Promote standby
            """),
        )
        parsed = parse_runbook(rb)
        assert parsed.id == "rds-multi-az-failover"
        assert parsed.triggers == ["rds_instance_unhealthy", "rds_failover_required"]
        assert parsed.estimated_duration == "15m"
        assert parsed.blast_radius == "single_db"
        assert parsed.approval_required is True
        assert "Promote standby" in parsed.body
        assert parsed.path == rb

    def test_parses_file_without_frontmatter(self, tmp_path: Path) -> None:
        from harness.core.runbook_loader import parse_runbook

        rb = _write_runbook(
            tmp_path,
            "plain.md",
            "# Plain Runbook\n\nNo frontmatter here.\n",
        )
        parsed = parse_runbook(rb)
        # ID defaults to filename stem
        assert parsed.id == "plain"
        assert parsed.triggers == []
        assert parsed.approval_required is False
        assert "Plain Runbook" in parsed.body

    def test_malformed_frontmatter_returns_none_or_degrades(self, tmp_path: Path) -> None:
        from harness.core.runbook_loader import parse_runbook

        rb = _write_runbook(
            tmp_path,
            "bad.md",
            "---\nnot: [valid yaml\n---\nbody\n",
        )
        # Must not crash; either returns a parsed runbook with empty metadata or None
        result = parse_runbook(rb)
        # The parser should fall back gracefully — id = filename, body preserved
        assert result is None or (result.id == "bad" and "body" in result.body)


class TestRunbookLibrary:
    def _make_library(self, tmp_path: Path) -> "RunbookLibrary":
        from harness.core.runbook_loader import RunbookLibrary

        _write_runbook(
            tmp_path,
            "rds_failover.md",
            dedent("""\
            ---
            id: rds-multi-az-failover
            triggers: [rds_instance_unhealthy]
            ---
            # RDS failover
            Promote the standby replica.
            """),
        )
        _write_runbook(
            tmp_path,
            "ecs_rollback.md",
            dedent("""\
            ---
            id: ecs-service-rollback
            triggers: [ecs_task_failure]
            ---
            # ECS rollback
            Revert to the previous task definition.
            """),
        )
        _write_runbook(
            tmp_path,
            "slo_overview.md",
            "# SLO Overview\n\nLatency budget: 1s.\n",
        )
        return RunbookLibrary.from_directory(tmp_path)

    def test_library_loads_all_md_files(self, tmp_path: Path) -> None:
        lib = self._make_library(tmp_path)
        assert len(lib.runbooks) == 3

    def test_library_indexed_by_id(self, tmp_path: Path) -> None:
        lib = self._make_library(tmp_path)
        rds = lib.get_by_id("rds-multi-az-failover")
        assert rds is not None
        assert rds.blast_radius is None  # not in frontmatter

    def test_search_by_trigger_returns_matches(self, tmp_path: Path) -> None:
        lib = self._make_library(tmp_path)
        hits = lib.search_by_triggers(["rds_instance_unhealthy"])
        assert len(hits) == 1
        assert hits[0].id == "rds-multi-az-failover"

    def test_search_by_trigger_is_union(self, tmp_path: Path) -> None:
        lib = self._make_library(tmp_path)
        hits = lib.search_by_triggers(["rds_instance_unhealthy", "ecs_task_failure"])
        ids = {h.id for h in hits}
        assert ids == {"rds-multi-az-failover", "ecs-service-rollback"}

    def test_search_by_query_returns_excerpts(self, tmp_path: Path) -> None:
        lib = self._make_library(tmp_path)
        hits = lib.search_by_query("standby", max_results=5)
        assert len(hits) >= 1
        assert any("standby" in h.excerpt.lower() for h in hits)
        # Each hit carries a source path
        for h in hits:
            assert h.source_path.name.endswith(".md")

    def test_search_by_query_respects_max_results(self, tmp_path: Path) -> None:
        lib = self._make_library(tmp_path)
        # "the" appears in multiple runbooks — max_results should clip
        hits = lib.search_by_query("the", max_results=1)
        assert len(hits) <= 1

    def test_empty_directory_yields_empty_library(self, tmp_path: Path) -> None:
        from harness.core.runbook_loader import RunbookLibrary

        (tmp_path / "runbooks").mkdir()
        lib = RunbookLibrary.from_directory(tmp_path / "runbooks")
        assert lib.runbooks == []
        assert lib.get_by_id("anything") is None
        assert lib.search_by_triggers(["x"]) == []
        assert lib.search_by_query("x") == []

    def test_nonexistent_directory_yields_empty_library(self, tmp_path: Path) -> None:
        from harness.core.runbook_loader import RunbookLibrary

        lib = RunbookLibrary.from_directory(tmp_path / "does_not_exist")
        assert lib.runbooks == []


class TestSearchHitShape:
    def test_hit_carries_required_fields(self, tmp_path: Path) -> None:
        from harness.core.runbook_loader import RunbookLibrary

        _write_runbook(
            tmp_path,
            "r.md",
            "---\nid: r\ntriggers: [t]\n---\n# Test\n\nKeyword match here.\n",
        )
        lib = RunbookLibrary.from_directory(tmp_path)
        hits = lib.search_by_query("keyword")
        assert len(hits) == 1
        hit = hits[0]
        assert hit.runbook_id == "r"
        assert isinstance(hit.source_path, Path)
        assert "keyword" in hit.excerpt.lower()
        assert 0 <= hit.relevance_score <= 1

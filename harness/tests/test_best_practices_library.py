"""Tests for best-practice library loader + Tier 1.5 overlay preload.

Covers:
  * Loading curated YAMLs under config/best_practices/ (backend, frontend
    ship today; schema file is skipped)
  * Principle parsing: severity filtering, by_id lookup, malformed entry
    handling
  * The `best_practices_overlay` preload source (populated / absent /
    empty file)
"""

from __future__ import annotations

from pathlib import Path

from harness.core.best_practices import (
    BestPracticeLibrary,
    Principle,
    list_available_industries,
    load_library,
)
from harness.core.manifest_loader import _build_preload_item


# ── Library discovery ────────────────────────────────────────────────────


class TestListAvailableIndustries:
    def test_lists_shipped_libraries(self) -> None:
        industries = list_available_industries()
        # We ship at least backend + frontend on this branch
        assert "backend" in industries
        assert "frontend" in industries

    def test_skips_schema_file(self) -> None:
        """config/best_practices/_schema.yaml is an author doc, not a library."""
        assert "_schema" not in list_available_industries()

    def test_missing_dir_returns_empty(self, tmp_path: Path) -> None:
        # Point at an empty directory — no libraries yet
        assert list_available_industries(library_dir=tmp_path) == []


# ── Library loading ──────────────────────────────────────────────────────


class TestLoadLibrary:
    def test_loads_backend_library(self) -> None:
        lib = load_library("backend")
        assert lib is not None
        assert lib.industry == "backend"
        assert lib.description  # non-empty
        # Backend library must carry structured_logging (P0 baseline)
        assert lib.by_id("structured_logging") is not None
        # And at least a couple of canonical_sources
        assert len(lib.canonical_sources) >= 2

    def test_loads_frontend_library(self) -> None:
        lib = load_library("frontend")
        assert lib is not None
        assert lib.industry == "frontend"
        assert lib.by_id("typescript_strict") is not None

    def test_unknown_industry_returns_none(self) -> None:
        assert load_library("there-is-no-such-industry-2026") is None

    def test_empty_industry_returns_none(self) -> None:
        assert load_library("") is None

    def test_schema_file_rejected(self) -> None:
        """Even though _schema.yaml exists, load_library('_schema') must refuse it."""
        assert load_library("_schema") is None

    def test_case_insensitive_industry(self) -> None:
        assert load_library("Backend") is not None
        assert load_library("BACKEND") is not None

    def test_custom_dir(self, tmp_path: Path) -> None:
        # A minimal hand-written library
        (tmp_path / "custom.yaml").write_text(
            "version: '1.0'\n"
            "industry: custom\n"
            "description: test\n"
            "principles:\n"
            "  - id: p1\n"
            "    title: Principle One\n"
            "    severity: must\n"
            "    why: because\n",
            encoding="utf-8",
        )
        lib = load_library("custom", library_dir=tmp_path)
        assert lib is not None
        assert len(lib.principles) == 1
        assert lib.principles[0].severity == "must"

    def test_unknown_severity_coerces_to_should(self, tmp_path: Path) -> None:
        (tmp_path / "custom.yaml").write_text(
            "industry: custom\n"
            "principles:\n"
            "  - id: p1\n"
            "    title: T\n"
            "    severity: quantum_maybe\n"
            "    why: x\n",
            encoding="utf-8",
        )
        lib = load_library("custom", library_dir=tmp_path)
        assert lib is not None
        assert lib.principles[0].severity == "should"

    def test_malformed_principle_skipped(self, tmp_path: Path) -> None:
        (tmp_path / "custom.yaml").write_text(
            "industry: custom\n"
            "principles:\n"
            "  - id: good\n"
            "    title: Good One\n"
            "    severity: must\n"
            "    why: x\n"
            "  - {}\n"                          # missing id + title
            "  - id: notitle\n"                  # missing title
            "    severity: must\n",
            encoding="utf-8",
        )
        lib = load_library("custom", library_dir=tmp_path)
        assert lib is not None
        assert len(lib.principles) == 1
        assert lib.principles[0].id == "good"

    def test_invalid_yaml_returns_none(self, tmp_path: Path) -> None:
        (tmp_path / "broken.yaml").write_text("not: valid: yaml: [", encoding="utf-8")
        assert load_library("broken", library_dir=tmp_path) is None


# ── Library query helpers ────────────────────────────────────────────────


class TestLibraryHelpers:
    def _sample(self) -> BestPracticeLibrary:
        return BestPracticeLibrary(
            industry="x",
            principles=[
                Principle(id="a", title="A", severity="must", why="x"),
                Principle(id="b", title="B", severity="should", why="y"),
                Principle(id="c", title="C", severity="must", why="z"),
                Principle(id="d", title="D", severity="nice", why="w"),
            ],
        )

    def test_by_severity_filters(self) -> None:
        lib = self._sample()
        must = lib.by_severity("must")
        assert {p.id for p in must} == {"a", "c"}

    def test_by_severity_case_insensitive(self) -> None:
        lib = self._sample()
        assert len(lib.by_severity("MUST")) == 2

    def test_by_id_lookup(self) -> None:
        lib = self._sample()
        found = lib.by_id("b")
        assert found is not None
        assert found.title == "B"
        assert lib.by_id("missing") is None

    def test_must_ids_set(self) -> None:
        assert self._sample().must_ids() == {"a", "c"}

    def test_principle_ids_full_set(self) -> None:
        assert self._sample().principle_ids() == {"a", "b", "c", "d"}


# ── Tier 1.5 preload ─────────────────────────────────────────────────────


class TestBestPracticesOverlayPreload:
    def test_populated_overlay_injected(self, tmp_path: Path) -> None:
        (tmp_path / "context").mkdir()
        overlay_path = tmp_path / "context" / "best_practices.md"
        overlay_path.write_text(
            "## Gap: structured_logging\nYour service is missing correlation IDs.",
            encoding="utf-8",
        )
        item = _build_preload_item("best_practices_overlay", tmp_path)
        assert item is not None
        assert item["source"] == "preload:best_practices_overlay"
        # Content preserves the body and wraps with a Tier 1.5 header
        assert "Tier 1.5" in item["content"]
        assert "structured_logging" in item["content"]
        # Priority sits between standards (10) and chunks (5)
        assert item["priority"] == 9

    def test_missing_overlay_returns_none(self, tmp_path: Path) -> None:
        """Domains generated before this feature have no overlay file — preload no-ops."""
        assert _build_preload_item("best_practices_overlay", tmp_path) is None

    def test_empty_overlay_returns_none(self, tmp_path: Path) -> None:
        (tmp_path / "context").mkdir()
        (tmp_path / "context" / "best_practices.md").write_text("   \n\n", encoding="utf-8")
        assert _build_preload_item("best_practices_overlay", tmp_path) is None

    def test_no_domain_root_returns_none(self) -> None:
        """Match the pattern other *_domain_* preloads use."""
        assert _build_preload_item("best_practices_overlay", None) is None

"""Tests for BestPracticeResearchAgent bundle + `best_practice_library` preload.

The agent itself is LLM-driven and exercised by the genesis e2e tests;
here we pin:

  * The bundle files exist and parse (manifest, prompt, grading)
  * The manifest is shaped correctly (single-shot, no tools, preload set)
  * The `best_practice_library` preload source resolves the industry
    from domain.yaml and renders the matching library to markdown
  * Missing domain.yaml / missing industry / unknown industry all
    gracefully return None
"""

from __future__ import annotations

from pathlib import Path

import yaml

from harness.core.manifest_loader import _build_preload_item


_AGENT_DIR = Path(__file__).resolve().parent.parent.parent / "agents" / "_genesis" / "BestPracticeResearchAgent" / "v1"


# ── Bundle shape ─────────────────────────────────────────────────────────


class TestResearchAgentBundle:
    def test_manifest_exists(self) -> None:
        assert (_AGENT_DIR / "agent_manifest.yaml").exists()

    def test_system_prompt_exists(self) -> None:
        assert (_AGENT_DIR / "system_prompt.md").exists()

    def test_grading_criteria_exists(self) -> None:
        assert (_AGENT_DIR / "grading_criteria.yaml").exists()

    def test_manifest_single_shot_no_tools(self) -> None:
        m = yaml.safe_load((_AGENT_DIR / "agent_manifest.yaml").read_text(encoding="utf-8"))
        exec_mode = m["execution_mode"]
        assert exec_mode["primary"] == "react"
        assert exec_mode["max_react_steps"] == 1
        assert m["tools"] == []

    def test_manifest_preloads_library(self) -> None:
        m = yaml.safe_load((_AGENT_DIR / "agent_manifest.yaml").read_text(encoding="utf-8"))
        preload = m.get("context", {}).get("preload", [])
        assert "best_practice_library" in preload

    def test_output_schema_matches_knowledge_mapper(self) -> None:
        """Downstream depends on the same shape — pin key fields."""
        m = yaml.safe_load((_AGENT_DIR / "agent_manifest.yaml").read_text(encoding="utf-8"))
        req = set(m["output_schema"]["required"])
        # Must carry knowledge_map, coverage, gaps like KnowledgeMapperAgent + research_notes extra
        assert {"knowledge_map", "coverage", "gaps", "research_notes"}.issubset(req)
        km_props = m["output_schema"]["properties"]["knowledge_map"]["properties"]
        assert "standards_sources" in km_props
        assert "workflow_processes" in km_props
        coverage_req = m["output_schema"]["properties"]["coverage"]["required"]
        assert {"standards", "workflows", "compliance", "tools", "roles", "overall"} == set(coverage_req)

    def test_manifest_readonly_permissions(self) -> None:
        m = yaml.safe_load((_AGENT_DIR / "agent_manifest.yaml").read_text(encoding="utf-8"))
        perms = m["permissions"]
        assert perms["file_edit"] == "deny"
        assert perms["file_create"] == "deny"
        assert perms["shell_command"] == "deny"
        assert perms["network_access"] == "deny"


# ── best_practice_library preload ───────────────────────────────────────


class TestBestPracticeLibraryPreload:
    def _domain(self, tmp_path: Path, industry: str) -> Path:
        (tmp_path / "domain.yaml").write_text(
            f"name: probe\nindustry: {industry}\n", encoding="utf-8",
        )
        return tmp_path

    def test_backend_library_rendered(self, tmp_path: Path) -> None:
        root = self._domain(tmp_path, "backend")
        item = _build_preload_item("best_practice_library", root)
        assert item is not None
        assert item["source"].startswith("preload:best_practice_library:")
        # The markdown body surfaces at least a couple of known principle ids
        assert "structured_logging" in item["content"]
        assert "secret_management" in item["content"]
        # Canonical sources section renders
        assert "Canonical sources" in item["content"]

    def test_frontend_library_rendered(self, tmp_path: Path) -> None:
        root = self._domain(tmp_path, "frontend")
        item = _build_preload_item("best_practice_library", root)
        assert item is not None
        assert "typescript_strict" in item["content"]

    def test_data_library_rendered(self, tmp_path: Path) -> None:
        root = self._domain(tmp_path, "data")
        item = _build_preload_item("best_practice_library", root)
        assert item is not None
        assert "idempotent_pipelines" in item["content"]

    def test_case_insensitive_industry(self, tmp_path: Path) -> None:
        root = self._domain(tmp_path, "Backend")
        item = _build_preload_item("best_practice_library", root)
        assert item is not None

    def test_unknown_industry_returns_none(self, tmp_path: Path) -> None:
        root = self._domain(tmp_path, "there-is-no-such-thing")
        assert _build_preload_item("best_practice_library", root) is None

    def test_missing_domain_yaml_returns_none(self, tmp_path: Path) -> None:
        # Clean tmp — no domain.yaml
        assert _build_preload_item("best_practice_library", tmp_path) is None

    def test_domain_yaml_without_industry_returns_none(self, tmp_path: Path) -> None:
        (tmp_path / "domain.yaml").write_text("name: probe\n", encoding="utf-8")
        assert _build_preload_item("best_practice_library", tmp_path) is None

    def test_no_domain_root_returns_none(self) -> None:
        assert _build_preload_item("best_practice_library", None) is None

    def test_malformed_domain_yaml_returns_none(self, tmp_path: Path) -> None:
        (tmp_path / "domain.yaml").write_text("not: valid: yaml: [", encoding="utf-8")
        assert _build_preload_item("best_practice_library", tmp_path) is None

    def test_library_content_includes_severity_labels(self, tmp_path: Path) -> None:
        root = self._domain(tmp_path, "backend")
        item = _build_preload_item("best_practice_library", root)
        assert item is not None
        # Backend library has "must" principles — severity should appear
        assert "_must_" in item["content"] or "must" in item["content"].lower()

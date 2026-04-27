"""Tests for AgentRegistry."""

from __future__ import annotations

import tempfile
from pathlib import Path

import yaml

from harness.core.agent_registry import AgentRegistry


class TestAgentRegistry:
    def test_register_and_lookup(self) -> None:
        reg = AgentRegistry()
        reg.register("backend/CodeGen/v1", {"id": "backend/CodeGen/v1", "domain": "backend", "category": "fast-codegen"})
        agent = reg.get_agent("backend/CodeGen/v1")
        assert agent is not None
        assert agent["domain"] == "backend"

    def test_lookup_missing(self) -> None:
        reg = AgentRegistry()
        assert reg.get_agent("nonexistent") is None

    def test_list_agents_all(self) -> None:
        reg = AgentRegistry()
        reg.register("a/A/v1", {"id": "a/A/v1", "domain": "a"})
        reg.register("b/B/v1", {"id": "b/B/v1", "domain": "b"})
        assert len(reg.list_agents()) == 2

    def test_list_agents_by_domain(self) -> None:
        reg = AgentRegistry()
        reg.register("a/A/v1", {"id": "a/A/v1", "domain": "a"})
        reg.register("a/B/v1", {"id": "a/B/v1", "domain": "a"})
        reg.register("b/C/v1", {"id": "b/C/v1", "domain": "b"})
        assert len(reg.list_agents(domain="a")) == 2
        assert len(reg.list_agents(domain="b")) == 1

    def test_list_domains(self) -> None:
        reg = AgentRegistry()
        reg.register("a/A/v1", {"id": "a/A/v1", "domain": "a"})
        reg.register("b/B/v1", {"id": "b/B/v1", "domain": "b"})
        assert sorted(reg.list_domains()) == ["a", "b"]

    def test_find_by_category(self) -> None:
        reg = AgentRegistry()
        reg.register("a/A/v1", {"id": "a/A/v1", "domain": "a", "category": "fast-codegen"})
        reg.register("a/B/v1", {"id": "a/B/v1", "domain": "a", "category": "reasoning"})
        assert reg.find_by_category("fast-codegen") == ["a/A/v1"]

    def test_find_by_pack(self) -> None:
        reg = AgentRegistry()
        reg.register("a/A/v1", {"id": "a/A/v1", "domain": "a", "pack": "core"})
        reg.register("a/B/v1", {"id": "a/B/v1", "domain": "a", "pack": "tools"})
        assert reg.find_by_pack("core") == ["a/A/v1"]

    def test_scan_fixture_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a fake agent manifest
            agent_dir = Path(tmpdir) / "backend" / "agents" / "CodeGen" / "v1"
            agent_dir.mkdir(parents=True)
            manifest = {"id": "backend/CodeGen/v1", "domain": "backend", "category": "fast-codegen"}
            (agent_dir / "agent_manifest.yaml").write_text(yaml.dump(manifest), encoding="utf-8")

            reg = AgentRegistry()
            reg.add_scan_path(tmpdir)
            count = reg.scan()
            assert count == 1
            assert reg.get_agent("backend/CodeGen/v1") is not None

    def test_scan_empty_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            reg = AgentRegistry()
            reg.add_scan_path(tmpdir)
            assert reg.scan() == 0

    def test_agent_count(self) -> None:
        reg = AgentRegistry()
        assert reg.agent_count == 0
        reg.register("a/A/v1", {"id": "a/A/v1", "domain": "a"})
        assert reg.agent_count == 1


# ───────────────────────────────────────────────────────────────────────────
# H-REG additions: path cache + resolve_path + ManifestLoader integration
# ───────────────────────────────────────────────────────────────────────────


def _write_agent_at(base: Path, agent_id: str) -> Path:
    """Write an agent_manifest.yaml at the conventional location."""
    parts = agent_id.split("/")
    if len(parts) >= 3:
        agent_dir = base / "agents" / parts[0] / parts[1] / parts[2]
    elif len(parts) == 2:
        agent_dir = base / "agents" / parts[0] / parts[1]
    else:
        agent_dir = base / "agents" / parts[0]
    agent_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "id": agent_id,
        "domain": parts[0] if len(parts) > 1 else "unknown",
        "category": "reasoning",
        "system_prompt_ref": "system_prompt.md",
    }
    (agent_dir / "agent_manifest.yaml").write_text(yaml.dump(manifest), encoding="utf-8")
    (agent_dir / "system_prompt.md").write_text("stub", encoding="utf-8")
    return agent_dir


class TestScanPopulatesPathCache:
    def test_scan_records_agent_paths(self, tmp_path: Path) -> None:
        _write_agent_at(tmp_path, "domain/A/v1")
        _write_agent_at(tmp_path, "domain/B/v1")
        reg = AgentRegistry()
        reg.add_scan_path(tmp_path)
        assert reg.scan() == 2
        assert reg.get_path("domain/A/v1") is not None
        assert reg.get_path("domain/B/v1") is not None

    def test_get_path_returns_directory(self, tmp_path: Path) -> None:
        _write_agent_at(tmp_path, "domain/A/v1")
        reg = AgentRegistry()
        reg.add_scan_path(tmp_path)
        reg.scan()
        path = reg.get_path("domain/A/v1")
        assert path is not None
        assert path.is_dir()
        assert (path / "agent_manifest.yaml").exists()

    def test_scan_skips_malformed_manifests(self, tmp_path: Path) -> None:
        _write_agent_at(tmp_path, "domain/Good/v1")
        bad_dir = tmp_path / "agents" / "domain" / "Bad" / "v1"
        bad_dir.mkdir(parents=True)
        (bad_dir / "agent_manifest.yaml").write_text(": :\n:: not yaml", encoding="utf-8")
        reg = AgentRegistry()
        reg.add_scan_path(tmp_path)
        assert reg.scan() == 1


class TestRegisterWithPath:
    def test_register_records_path_when_provided(self, tmp_path: Path) -> None:
        reg = AgentRegistry()
        reg.register("d/X/v1", {"id": "d/X/v1", "domain": "d"}, path=tmp_path)
        assert reg.get_path("d/X/v1") == tmp_path

    def test_register_without_path_returns_none_for_path(self) -> None:
        reg = AgentRegistry()
        reg.register("d/X/v1", {"id": "d/X/v1", "domain": "d"})
        assert reg.get_path("d/X/v1") is None


class TestResolvePath:
    def test_cache_hit_returned_directly(self, tmp_path: Path) -> None:
        agent_dir = _write_agent_at(tmp_path, "d/A/v1")
        reg = AgentRegistry()
        reg.add_scan_path(tmp_path)
        reg.scan()
        assert reg.resolve_path("d/A/v1", domain_root=tmp_path) == agent_dir

    def test_domain_root_agentname_version_layout(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / "agents" / "CodeGen" / "v1"
        agent_dir.mkdir(parents=True)
        (agent_dir / "agent_manifest.yaml").write_text(
            yaml.dump({"id": "backend/CodeGen/v1"}), encoding="utf-8"
        )
        reg = AgentRegistry()
        assert reg.resolve_path("backend/CodeGen/v1", domain_root=tmp_path) == agent_dir

    def test_domain_root_with_sub_prefix(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / "agents" / "backend" / "CodeGen" / "v1"
        agent_dir.mkdir(parents=True)
        (agent_dir / "agent_manifest.yaml").write_text(
            yaml.dump({"id": "backend/CodeGen/v1"}), encoding="utf-8"
        )
        reg = AgentRegistry()
        assert reg.resolve_path("backend/CodeGen/v1", domain_root=tmp_path) == agent_dir

    def test_project_root_sub_layout(self, tmp_path: Path) -> None:
        project_root = tmp_path / "project"
        agent_dir = project_root / "agents" / "_shared" / "CodeWriter" / "v1"
        agent_dir.mkdir(parents=True)
        (agent_dir / "agent_manifest.yaml").write_text(
            yaml.dump({"id": "_shared/CodeWriter/v1"}), encoding="utf-8"
        )
        domain_root = tmp_path / "domain"
        domain_root.mkdir()
        reg = AgentRegistry()
        assert reg.resolve_path(
            "_shared/CodeWriter/v1",
            domain_root=domain_root,
            project_root=project_root,
        ) == agent_dir

    def test_unknown_agent_returns_none(self, tmp_path: Path) -> None:
        reg = AgentRegistry()
        assert reg.resolve_path("nonexistent/agent/v99", domain_root=tmp_path) is None

    def test_fallback_hit_is_cached(self, tmp_path: Path) -> None:
        """First resolve does the 3-path scan; second is O(1) cache lookup."""
        agent_dir = tmp_path / "agents" / "Unknown" / "v1"
        agent_dir.mkdir(parents=True)
        (agent_dir / "agent_manifest.yaml").write_text(
            yaml.dump({"id": "d/Unknown/v1"}), encoding="utf-8"
        )
        reg = AgentRegistry()
        assert reg.get_path("d/Unknown/v1") is None  # not cached yet
        assert reg.resolve_path("d/Unknown/v1", domain_root=tmp_path) == agent_dir
        assert reg.get_path("d/Unknown/v1") == agent_dir  # now cached

    def test_stale_cache_refreshes_from_disk(self, tmp_path: Path) -> None:
        """A cached path that no longer exists triggers a re-resolve."""
        import shutil
        agent_dir = _write_agent_at(tmp_path, "d/Z/v1")
        reg = AgentRegistry()
        reg.register("d/Z/v1", {"id": "d/Z/v1"}, path=agent_dir)
        shutil.rmtree(agent_dir)
        # Re-create so the fallback can find it
        _write_agent_at(tmp_path, "d/Z/v1")
        resolved = reg.resolve_path("d/Z/v1", domain_root=tmp_path)
        assert resolved is not None and resolved.exists()


class TestClearResets:
    def test_clear_empties_paths_too(self, tmp_path: Path) -> None:
        _write_agent_at(tmp_path, "d/A/v1")
        reg = AgentRegistry()
        reg.add_scan_path(tmp_path)
        reg.scan()
        reg.clear()
        assert reg.agent_count == 0
        assert reg.get_path("d/A/v1") is None


class TestManifestLoaderIntegration:
    """ManifestLoader.build_step_configs uses the registry for O(1) lookup."""

    def test_pre_scanned_registry_used(self, tmp_path: Path) -> None:
        from harness.core.manifest_loader import ManifestLoader
        agent_dir = _write_agent_at(tmp_path, "domain/X/v1")
        reg = AgentRegistry()
        reg.register("domain/X/v1", {"id": "domain/X/v1"}, path=agent_dir)
        loader = ManifestLoader(registry=reg)

        workflow = {
            "name": "wf",
            "domain": "domain",
            "steps": [{"name": "s", "agent": "domain/X/v1"}],
        }
        configs = loader.build_step_configs(workflow, tmp_path, domain_manifest={})
        assert configs["s"]["manifest"]["id"] == "domain/X/v1"

    def test_empty_registry_still_resolves_via_fallback(self, tmp_path: Path) -> None:
        from harness.core.manifest_loader import ManifestLoader
        _write_agent_at(tmp_path, "domain/Y/v1")
        loader = ManifestLoader()  # no registry passed → lazy-created
        workflow = {
            "name": "wf",
            "domain": "domain",
            "steps": [{"name": "s", "agent": "domain/Y/v1"}],
        }
        configs = loader.build_step_configs(workflow, tmp_path, domain_manifest={})
        assert configs["s"]["manifest"]["id"] == "domain/Y/v1"

    def test_unknown_agent_emits_stub_manifest(self, tmp_path: Path) -> None:
        from harness.core.manifest_loader import ManifestLoader
        loader = ManifestLoader()
        workflow = {
            "name": "wf",
            "domain": "d",
            "steps": [{"name": "s", "agent": "does/not/exist"}],
        }
        configs = loader.build_step_configs(workflow, tmp_path, domain_manifest={})
        assert configs["s"]["manifest"] == {"id": "does/not/exist"}

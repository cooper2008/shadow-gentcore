"""Tests for AgentRegistry."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
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

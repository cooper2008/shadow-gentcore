"""Tests for MemoryRecallAdapter (Tier 4 tool).

Covers the root-path resolution precedence:
  1. explicit `memory_root` / `domain_root` argument
  2. `GENTCORE_MEMORY_DIR` env var
  3. default `.gentcore/memory` under CWD

And the empty-memory / entry-rendering behaviour.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from harness.core.memory_store import FileMemoryStore
from harness.tools.builtin import MemoryRecallAdapter


def _seed(store_root: Path, agent_id: str, key: str, value: str) -> None:
    """Seed a single memory entry the adapter should find."""
    store = FileMemoryStore(base_dir=store_root)
    store.store(agent_id=agent_id, key=key, value=value)


def _invoke(args: dict) -> dict:
    return asyncio.run(MemoryRecallAdapter().invoke("memory_recall", args))


class TestRootPathResolution:
    def test_explicit_memory_root_wins(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Explicit memory_root beats env var."""
        explicit = tmp_path / "explicit" / ".gentcore" / "memory"
        _seed(explicit, "Ag", "run_output", "via-explicit")

        env_memory = tmp_path / "env" / ".gentcore" / "memory"
        _seed(env_memory, "Ag", "run_output", "via-env")
        monkeypatch.setenv("GENTCORE_MEMORY_DIR", str(tmp_path / "env"))

        result = _invoke({"agent_id": "Ag", "memory_root": str(tmp_path / "explicit")})
        assert result["success"] is True
        assert "via-explicit" in result["stdout"]
        assert "via-env" not in result["stdout"]

    def test_domain_root_alias(self, tmp_path: Path) -> None:
        """`domain_root` is a synonym for `memory_root`."""
        store_root = tmp_path / ".gentcore" / "memory"
        _seed(store_root, "Ag", "run_output", "via-domain-root")

        result = _invoke({"agent_id": "Ag", "domain_root": str(tmp_path)})
        assert result["success"] is True
        assert "via-domain-root" in result["stdout"]

    def test_env_var_fallback(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """When no memory_root/domain_root arg, GENTCORE_MEMORY_DIR is honoured."""
        env_memory = tmp_path / ".gentcore" / "memory"
        _seed(env_memory, "Ag", "run_output", "via-env-var")
        monkeypatch.setenv("GENTCORE_MEMORY_DIR", str(tmp_path))

        result = _invoke({"agent_id": "Ag"})
        assert result["success"] is True
        assert "via-env-var" in result["stdout"]

    def test_env_var_with_memory_suffix(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """GENTCORE_MEMORY_DIR pointing directly at a 'memory' dir is used as-is."""
        direct = tmp_path / "memory"
        _seed(direct, "Ag", "run_output", "via-direct-memory")
        monkeypatch.setenv("GENTCORE_MEMORY_DIR", str(direct))

        result = _invoke({"agent_id": "Ag"})
        assert result["success"] is True
        assert "via-direct-memory" in result["stdout"]


class TestEmptyMemory:
    def test_missing_agent_id_errors(self) -> None:
        result = _invoke({})
        assert result["success"] is False
        assert "agent_id" in result["stderr"]

    def test_no_past_entries_returns_ok_with_hint(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GENTCORE_MEMORY_DIR", str(tmp_path))
        result = _invoke({"agent_id": "FreshAgent"})
        assert result["success"] is True
        assert result["entries_returned"] == 0
        assert "first run" in result["stdout"].lower()


class TestBuildMemoryStoreHelper:
    """build_memory_store from manifest_loader — the factory that wires
    AgentRunner's persistent memory at boot_engine time."""

    def test_default_is_per_domain(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GENTCORE_MEMORY_DIR", raising=False)
        from harness.core.manifest_loader import build_memory_store
        store = build_memory_store(tmp_path)
        assert store is not None
        store.store(agent_id="A", key="k", value="v")
        assert (tmp_path / ".gentcore" / "memory" / "A" / "memories.jsonl").exists()

    def test_env_var_overrides_domain(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """GENTCORE_MEMORY_DIR redirects memory outside the domain — for
        cross-domain (genesis) memory sharing."""
        shared = tmp_path / "shared-genesis-memory"
        domain = tmp_path / "some-domain"
        domain.mkdir()
        monkeypatch.setenv("GENTCORE_MEMORY_DIR", str(shared))
        from harness.core.manifest_loader import build_memory_store
        store = build_memory_store(domain)
        store.store(agent_id="GenesisAgent", key="learned", value="pattern")
        assert (shared / ".gentcore" / "memory" / "GenesisAgent" / "memories.jsonl").exists()
        assert not (domain / ".gentcore").exists()  # domain NOT polluted

    def test_env_var_pointing_at_memory_dir_used_as_is(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        direct = tmp_path / "memory"
        monkeypatch.setenv("GENTCORE_MEMORY_DIR", str(direct))
        from harness.core.manifest_loader import build_memory_store
        store = build_memory_store(tmp_path / "irrelevant")
        store.store(agent_id="A", key="k", value="v")
        assert (direct / "A" / "memories.jsonl").exists()


class TestEntryRendering:
    def test_key_filter_narrows_results(self, tmp_path: Path) -> None:
        store_root = tmp_path / ".gentcore" / "memory"
        _seed(store_root, "Ag", "run_output", "A")
        _seed(store_root, "Ag", "scratch", "B")
        result = _invoke({"agent_id": "Ag", "memory_root": str(tmp_path), "key": "run_output"})
        assert "A" in result["stdout"]
        assert "B" not in result["stdout"]

    def test_k_limits_entries(self, tmp_path: Path) -> None:
        store_root = tmp_path / ".gentcore" / "memory"
        for i in range(10):
            _seed(store_root, "Ag", "run_output", f"entry-{i}")
        result = _invoke({"agent_id": "Ag", "memory_root": str(tmp_path), "k": 2})
        assert result["entries_returned"] == 2
        # Newest (9) should appear, oldest (0) should not
        assert "entry-9" in result["stdout"]
        assert "entry-0" not in result["stdout"]

    def test_json_value_rendered_as_preview(self, tmp_path: Path) -> None:
        store_root = tmp_path / ".gentcore" / "memory"
        payload = {"foo": "bar", "nested": {"x": 1}}
        _seed(store_root, "Ag", "run_output", json.dumps(payload))
        result = _invoke({"agent_id": "Ag", "memory_root": str(tmp_path)})
        assert "foo" in result["stdout"]

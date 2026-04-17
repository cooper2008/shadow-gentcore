"""Tests for B10 — JITAgentSynthesizer (experimental, flag-gated)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from harness.core.jit_agent import (
    JITAgentSynthesizer,
    JITSynthesisDenied,
    is_enabled,
)


# ── Flag gating ─────────────────────────────────────────────────────────────


class TestFlagGating:
    def test_flag_off_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GENTCORE_ALLOW_JIT_AGENT", raising=False)
        assert is_enabled() is False

    def test_truthy_values_enable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for truthy in ("1", "true", "TRUE", "yes", "on"):
            monkeypatch.setenv("GENTCORE_ALLOW_JIT_AGENT", truthy)
            assert is_enabled() is True, f"failed for {truthy!r}"

    def test_falsy_values_stay_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for falsy in ("0", "false", "no", "", "disabled"):
            monkeypatch.setenv("GENTCORE_ALLOW_JIT_AGENT", falsy)
            assert is_enabled() is False, f"failed for {falsy!r}"

    def test_synthesize_denied_when_flag_off(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("GENTCORE_ALLOW_JIT_AGENT", raising=False)
        synth = JITAgentSynthesizer(scratch_dir=tmp_path)
        with pytest.raises(JITSynthesisDenied):
            synth.synthesize("d/X/v1", goal="test")

    def test_synthesize_works_when_flag_on(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GENTCORE_ALLOW_JIT_AGENT", "1")
        synth = JITAgentSynthesizer(scratch_dir=tmp_path)
        result = synth.synthesize("d/X/v1", goal="test")
        assert result["mode"] == "stub"

    def test_force_enabled_bypasses_flag(self, tmp_path: Path) -> None:
        """Tests can instantiate with force_enabled=True to skip env lookup."""
        synth = JITAgentSynthesizer(scratch_dir=tmp_path, force_enabled=True)
        result = synth.synthesize("d/Y/v1", goal="force test")
        assert result["mode"] == "stub"


# ── Stub-mode synthesis ─────────────────────────────────────────────────────


class TestStubSynthesis:
    def _synth(self, tmp_path: Path) -> JITAgentSynthesizer:
        return JITAgentSynthesizer(scratch_dir=tmp_path, force_enabled=True)

    def test_returns_manifest_path_and_mode(self, tmp_path: Path) -> None:
        synth = self._synth(tmp_path)
        result = synth.synthesize("d/Foo/v1", goal="testing")
        assert result["mode"] == "stub"
        assert "manifest" in result
        assert "path" in result

    def test_manifest_has_required_fields(self, tmp_path: Path) -> None:
        synth = self._synth(tmp_path)
        result = synth.synthesize("d/Foo/v1", goal="testing")
        m = result["manifest"]
        for required in ("id", "domain", "category", "version", "system_prompt_ref",
                          "execution_mode", "tools", "permissions",
                          "input_schema", "output_schema"):
            assert required in m, f"missing {required}"

    def test_manifest_id_matches_request(self, tmp_path: Path) -> None:
        synth = self._synth(tmp_path)
        result = synth.synthesize("d/Foo/v1", goal="x")
        assert result["manifest"]["id"] == "d/Foo/v1"
        assert result["manifest"]["domain"] == "d"

    def test_manifest_has_default_deny_permissions(self, tmp_path: Path) -> None:
        """Safety: JIT agents start with no write/shell/network by default."""
        synth = self._synth(tmp_path)
        result = synth.synthesize("d/Foo/v1")
        perms = result["manifest"]["permissions"]
        assert perms["file_edit"] == "deny"
        assert perms["file_create"] == "deny"
        assert perms["shell_command"] == "deny"
        assert perms["network_access"] == "deny"

    def test_manifest_has_no_declared_tools(self, tmp_path: Path) -> None:
        """Stub agents ship with empty tools — caller adds them explicitly."""
        synth = self._synth(tmp_path)
        result = synth.synthesize("d/Foo/v1")
        assert result["manifest"]["tools"] == []

    def test_metadata_flags_synthesis_origin(self, tmp_path: Path) -> None:
        synth = self._synth(tmp_path)
        result = synth.synthesize("d/Foo/v1", goal="g")
        assert result["manifest"]["metadata"]["synthesised_by"] == "JITAgentSynthesizer"
        assert result["manifest"]["metadata"]["mode"] == "stub"

    def test_manifest_file_written_to_disk(self, tmp_path: Path) -> None:
        synth = self._synth(tmp_path)
        result = synth.synthesize("d/Foo/v1", goal="g")
        manifest_path = result["path"] / "agent_manifest.yaml"
        assert manifest_path.exists()
        loaded = yaml.safe_load(manifest_path.read_text())
        assert loaded["id"] == "d/Foo/v1"

    def test_system_prompt_written(self, tmp_path: Path) -> None:
        synth = self._synth(tmp_path)
        result = synth.synthesize("d/Foo/v1", goal="Do a thing")
        prompt_path = result["path"] / "system_prompt.md"
        assert prompt_path.exists()
        content = prompt_path.read_text()
        assert "Foo" in content
        assert "Do a thing" in content

    def test_rejects_malformed_agent_id(self, tmp_path: Path) -> None:
        """agent_id must have 3 parts: <domain>/<Name>/<version>."""
        synth = self._synth(tmp_path)
        with pytest.raises(ValueError):
            synth.synthesize("bad-id")
        with pytest.raises(ValueError):
            synth.synthesize("only/two")


# ── Factory-mode synthesis ─────────────────────────────────────────────────


class TestFactorySynthesis:
    def test_delegates_to_factory_runner(self, tmp_path: Path) -> None:
        captured: dict[str, Any] = {}

        def fake_runner(recipe: dict[str, Any]) -> dict[str, Any]:
            captured.update(recipe)
            return {
                "manifest": {
                    "id": recipe["agent_id"],
                    "domain": "d",
                    "category": "reasoning",
                },
                "path": tmp_path / "factory_output",
            }

        synth = JITAgentSynthesizer(factory_runner=fake_runner, force_enabled=True)
        result = synth.synthesize(
            "d/Novel/v1",
            goal="Handle a novel stage",
            context={"standards": "be careful", "tools": ["x"]},
        )

        assert result["mode"] == "factory"
        assert result["manifest"]["id"] == "d/Novel/v1"
        # Recipe was properly populated
        assert captured["agent_id"] == "d/Novel/v1"
        assert captured["goal"] == "Handle a novel stage"
        assert captured["context"]["standards"] == "be careful"
        assert captured["tools"] == ["x"]

    def test_factory_runner_invalid_payload_raises(self, tmp_path: Path) -> None:
        def bad_runner(recipe: dict[str, Any]) -> dict[str, Any]:
            return {"oops": "no manifest key"}

        synth = JITAgentSynthesizer(factory_runner=bad_runner, force_enabled=True)
        with pytest.raises(RuntimeError, match="missing 'manifest'"):
            synth.synthesize("d/N/v1", goal="x")

    def test_factory_runner_preserves_custom_mode_if_set(self, tmp_path: Path) -> None:
        """If the runner sets its own `mode`, synthesizer doesn't overwrite."""

        def runner(recipe: dict[str, Any]) -> dict[str, Any]:
            return {
                "manifest": {"id": recipe["agent_id"]},
                "path": tmp_path,
                "mode": "custom-factory-variant",
            }

        synth = JITAgentSynthesizer(factory_runner=runner, force_enabled=True)
        result = synth.synthesize("d/A/v1")
        assert result["mode"] == "custom-factory-variant"


# ── End-to-end flag-off safety ─────────────────────────────────────────────


class TestProductionSafety:
    def test_default_state_denies_synthesis(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Zero-config import: with no env var + no force_enabled, any
        `synthesize` call raises. This is the production default — no
        JIT agents can appear by accident."""
        monkeypatch.delenv("GENTCORE_ALLOW_JIT_AGENT", raising=False)
        synth = JITAgentSynthesizer(scratch_dir=tmp_path)  # no force_enabled
        with pytest.raises(JITSynthesisDenied):
            synth.synthesize("d/X/v1")

    def test_is_enabled_respects_runtime_env_mutation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Reads env on every call so changes take effect immediately."""
        monkeypatch.delenv("GENTCORE_ALLOW_JIT_AGENT", raising=False)
        assert is_enabled() is False
        monkeypatch.setenv("GENTCORE_ALLOW_JIT_AGENT", "1")
        assert is_enabled() is True
        monkeypatch.setenv("GENTCORE_ALLOW_JIT_AGENT", "0")
        assert is_enabled() is False

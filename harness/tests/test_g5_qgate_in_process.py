"""Tests for G5 — QualityGate uses in-process genesis_verifier (fix/G5-qgate-in-process).

Before G5: QualityGateAgent shelled out to `./ai validate` via shell_exec.
After G5: a Python-only builtin tool `verify_genesis_output` wraps the
in-process `harness.core.genesis_verifier.verify_genesis_output()` function,
and QualityGateAgent declares that tool instead of shell_exec.

Benefits: no subprocess fork, no CLI-on-PATH assumption, tighter permissions
(no shell_command allow needed), deterministic output schema.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
QGATE_DIR = REPO_ROOT / "agents" / "_genesis" / "QualityGateAgent" / "v1"


class TestBuiltinToolRegistration:
    def test_verify_genesis_output_is_approved(self) -> None:
        from harness.tools.builtin import is_approved_tool

        assert is_approved_tool("verify_genesis_output"), (
            "G5 requires verify_genesis_output to be in the approved tool lockdown set"
        )

    def test_adapter_registered_in_builtins(self) -> None:
        from harness.tools.builtin import BUILTIN_ADAPTERS

        assert "verify_genesis_output" in BUILTIN_ADAPTERS


class TestVerifyGenesisOutputAdapter:
    @pytest.mark.asyncio
    async def test_returns_passed_for_minimal_valid_domain(self, tmp_path: Path) -> None:
        from harness.tools.builtin import BUILTIN_ADAPTERS

        self._make_minimal_domain(tmp_path)
        adapter = BUILTIN_ADAPTERS["verify_genesis_output"]
        result = await adapter.invoke(
            "verify_genesis_output",
            {"domain_dir": str(tmp_path)},
        )
        assert result["success"] is True
        assert result["exit_code"] == 0
        # Parse the structured payload from stdout
        import json
        payload = json.loads(result["stdout"])
        assert payload["passed"] is True
        assert payload["failure_count"] == 0
        assert payload["total_checks"] >= 1

    @pytest.mark.asyncio
    async def test_returns_failed_for_missing_agents_dir(self, tmp_path: Path) -> None:
        from harness.tools.builtin import BUILTIN_ADAPTERS

        # No agents dir at all
        (tmp_path / "context").mkdir()
        (tmp_path / "context" / "standards.md").write_text("# Standards")
        adapter = BUILTIN_ADAPTERS["verify_genesis_output"]
        result = await adapter.invoke(
            "verify_genesis_output",
            {"domain_dir": str(tmp_path)},
        )
        import json
        payload = json.loads(result["stdout"])
        assert payload["passed"] is False
        assert any("agents/" in f for f in payload["failures"])

    @pytest.mark.asyncio
    async def test_missing_domain_dir_is_reported_cleanly(self, tmp_path: Path) -> None:
        from harness.tools.builtin import BUILTIN_ADAPTERS

        adapter = BUILTIN_ADAPTERS["verify_genesis_output"]
        result = await adapter.invoke(
            "verify_genesis_output",
            {"domain_dir": str(tmp_path / "does_not_exist")},
        )
        # Either success=False with a clean error, OR success=True with structural failures
        # (either is acceptable — the adapter must not raise or return malformed output)
        assert "stdout" in result
        assert "exit_code" in result

    @pytest.mark.asyncio
    async def test_adapter_rejects_missing_domain_dir_arg(self) -> None:
        from harness.tools.builtin import BUILTIN_ADAPTERS

        adapter = BUILTIN_ADAPTERS["verify_genesis_output"]
        result = await adapter.invoke("verify_genesis_output", {})
        assert result["success"] is False
        assert result["exit_code"] != 0

    @staticmethod
    def _make_minimal_domain(tmp_path: Path) -> None:
        agents = tmp_path / "agents" / "TestAgent" / "v1"
        agents.mkdir(parents=True)
        (agents / "agent_manifest.yaml").write_text(
            "id: test/TestAgent/v1\ndomain: test\ncategory: reasoning\n"
            "system_prompt_ref: system_prompt.md\n"
        )
        (agents / "system_prompt.md").write_text("# Test")
        workflows = tmp_path / "workflows"
        workflows.mkdir()
        (workflows / "wf.yaml").write_text(
            "name: wf\ndomain: test\nsteps:\n  - name: s\n    agent: test/TestAgent/v1\n"
        )
        ctx = tmp_path / "context"
        ctx.mkdir()
        (ctx / "standards.md").write_text("# Standards")


class TestQualityGateManifestUsesNewTool:
    def _manifest(self) -> dict:
        return yaml.safe_load((QGATE_DIR / "agent_manifest.yaml").read_text(encoding="utf-8"))

    def test_manifest_declares_verify_genesis_output(self) -> None:
        m = self._manifest()
        names = {t["name"] for t in m["tools"]}
        assert "verify_genesis_output" in names, (
            "QualityGateAgent must declare the verify_genesis_output tool"
        )

    def test_manifest_no_longer_requires_shell_exec(self) -> None:
        m = self._manifest()
        names = {t["name"] for t in m["tools"]}
        assert "shell_exec" not in names, (
            "QualityGateAgent should no longer need shell_exec after G5 — "
            "in-process verifier replaces `./ai validate` shell call"
        )

    def test_manifest_tightens_shell_permission_to_deny(self) -> None:
        m = self._manifest()
        assert m["permissions"]["shell_command"] == "deny", (
            "QualityGateAgent should deny shell_command after G5"
        )


class TestQualityGatePromptUpdated:
    def test_prompt_references_verify_genesis_output_tool(self) -> None:
        prompt = (QGATE_DIR / "system_prompt.md").read_text(encoding="utf-8")
        assert "verify_genesis_output" in prompt, (
            "QualityGateAgent prompt must tell the LLM to call verify_genesis_output"
        )

    def test_prompt_no_longer_mentions_ai_validate_shell(self) -> None:
        prompt = (QGATE_DIR / "system_prompt.md").read_text(encoding="utf-8")
        # The old guidance ran ``./ai validate`` via shell_exec. After G5 that
        # path is gone; remaining mentions should be in historical context only.
        assert "./ai validate" not in prompt, (
            "After G5, prompt should not instruct agents to shell out to ./ai validate"
        )

"""Tests for Phase 7: DAG execution, CloudRuntime, CI hooks, cross-domain, maintenance agents."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from harness.core.composition_engine import CompositionEngine, GateFailure
from harness.core.runtime import LocalRuntime, CloudRuntime
from harness.core.ci_hooks import CIHookRegistry
from harness.core.cross_domain import DomainRegistry


# ─── DAG Execution (task 89) ─────────────────────────────────────────────────


class TestDAGExecution:
    def test_topological_sort_linear(self) -> None:
        steps = [
            {"name": "a", "depends_on": []},
            {"name": "b", "depends_on": ["a"]},
            {"name": "c", "depends_on": ["b"]},
        ]
        layers = CompositionEngine.topological_sort(steps)
        assert layers == [["a"], ["b"], ["c"]]

    def test_topological_sort_diamond(self) -> None:
        steps = [
            {"name": "root", "depends_on": []},
            {"name": "left", "depends_on": ["root"]},
            {"name": "right", "depends_on": ["root"]},
            {"name": "join", "depends_on": ["left", "right"]},
        ]
        layers = CompositionEngine.topological_sort(steps)
        assert layers[0] == ["root"]
        assert set(layers[1]) == {"left", "right"}
        assert layers[2] == ["join"]

    def test_topological_sort_parallel(self) -> None:
        steps = [
            {"name": "a", "depends_on": []},
            {"name": "b", "depends_on": []},
            {"name": "c", "depends_on": []},
        ]
        layers = CompositionEngine.topological_sort(steps)
        assert len(layers) == 1
        assert set(layers[0]) == {"a", "b", "c"}

    def test_topological_sort_cycle_raises(self) -> None:
        steps = [
            {"name": "a", "depends_on": ["b"]},
            {"name": "b", "depends_on": ["a"]},
        ]
        with pytest.raises(ValueError, match="Cyclic"):
            CompositionEngine.topological_sort(steps)

    @pytest.mark.asyncio
    async def test_execute_dag_diamond(self) -> None:
        engine = CompositionEngine()
        steps = [
            {"name": "root", "depends_on": []},
            {"name": "left", "depends_on": ["root"]},
            {"name": "right", "depends_on": ["root"]},
            {"name": "join", "depends_on": ["left", "right"]},
        ]
        result = await engine.execute_dag(steps)
        assert result["status"] == "completed"
        assert set(result["step_results"].keys()) == {"root", "left", "right", "join"}

    @pytest.mark.asyncio
    async def test_execute_dag_parallel_steps_both_complete(self) -> None:
        engine = CompositionEngine()
        steps = [
            {"name": "a", "depends_on": []},
            {"name": "b", "depends_on": []},
        ]
        result = await engine.execute_dag(steps)
        assert result["status"] == "completed"
        assert "a" in result["step_results"]
        assert "b" in result["step_results"]

    @pytest.mark.asyncio
    async def test_execute_dag_cyclic_returns_error(self) -> None:
        engine = CompositionEngine()
        steps = [
            {"name": "x", "depends_on": ["y"]},
            {"name": "y", "depends_on": ["x"]},
        ]
        result = await engine.execute_dag(steps)
        assert result["status"] == "error"
        assert "Cyclic" in result["error"]

    def test_checkpoint_and_restore(self) -> None:
        engine = CompositionEngine()
        engine._step_results["step1"] = {"output": "done"}
        engine._execution_log.append({"event": "step_completed", "step": "step1"})

        ckpt = engine.checkpoint_state()
        engine.reset()
        assert engine.step_results == {}

        engine.restore_state(ckpt)
        assert "step1" in engine.step_results
        assert any(e["event"] == "state_restored_from_checkpoint" for e in engine.execution_log)


# ─── CloudRuntime (task 90) ──────────────────────────────────────────────────


class TestCloudRuntime:
    def test_output_mode_json(self) -> None:
        rt = CloudRuntime()
        assert rt.get_output_mode() == "json"

    def test_fail_closed_deny_unknown(self) -> None:
        rt = CloudRuntime()
        assert rt.resolve_permissions("file_write", "agent/A/v1") == "deny"

    def test_fail_closed_allow_listed(self) -> None:
        rt = CloudRuntime(allowed_actions={"file_read", "run_test"})
        assert rt.resolve_permissions("file_read", "agent/A/v1") == "allow"
        assert rt.resolve_permissions("file_write", "agent/A/v1") == "deny"

    def test_credentials_from_secret_manager(self, tmp_path: Path) -> None:
        secrets = {"anthropic": "sk-test-key"}
        rt = CloudRuntime(secret_manager=secrets)
        creds = rt.resolve_credentials("anthropic")
        assert creds["api_key"] == "sk-test-key"

    def test_credentials_missing_returns_empty(self) -> None:
        rt = CloudRuntime(secret_manager={})
        creds = rt.resolve_credentials("openai")
        assert creds["api_key"] == ""

    def test_webhook_notification(self) -> None:
        rt = CloudRuntime(webhook_url="https://hooks.example.com/agent")
        rt.notify_webhook("workflow_completed", {"workflow": "ci_pipeline", "status": "pass"})
        assert len(rt.notifications) == 1
        assert rt.notifications[0]["event"] == "workflow_completed"
        assert rt.notifications[0]["webhook_url"] == "https://hooks.example.com/agent"

    def test_resolve_workspace(self, tmp_path: Path) -> None:
        rt = CloudRuntime(workspace_root=tmp_path)
        ws = rt.resolve_workspace("backend")
        assert ws.exists()
        assert ws.name == "backend"

    def test_local_runtime_ask_permission(self, tmp_path: Path) -> None:
        rt = LocalRuntime(workspace_root=tmp_path)
        assert rt.resolve_permissions("any_action", "agent/X/v1") == "ask"


# ─── CI Hooks (task 94 / 95) ─────────────────────────────────────────────────


class TestCIHooks:
    @pytest.mark.asyncio
    async def test_register_and_trigger(self) -> None:
        reg = CIHookRegistry()
        reg.register("push", "ci_pipeline")

        results = await reg.process_event("push", {"branch": "main"})
        assert len(results) == 1
        assert results[0]["workflow"] == "ci_pipeline"
        assert results[0]["status"] == "no_executor"

    @pytest.mark.asyncio
    async def test_filter_fn_skips_non_matching(self) -> None:
        reg = CIHookRegistry()
        reg.register("push", "deploy", filter_fn=lambda p: p.get("branch") == "main")

        results = await reg.process_event("push", {"branch": "feature/x"})
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_filter_fn_matches(self) -> None:
        reg = CIHookRegistry()
        reg.register("push", "deploy", filter_fn=lambda p: p.get("branch") == "main")

        results = await reg.process_event("push", {"branch": "main"})
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_executor_called(self) -> None:
        reg = CIHookRegistry()
        reg.register("pull_request", "review_workflow")

        calls: list[str] = []

        async def mock_executor(wf_name: str, payload: dict[str, Any]) -> dict[str, Any]:
            calls.append(wf_name)
            return {"status": "completed"}

        results = await reg.process_event("pull_request", {"pr": 42}, executor=mock_executor)
        assert calls == ["review_workflow"]
        assert results[0]["status"] == "executed"
        assert results[0]["result"]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_executor_error_recorded(self) -> None:
        reg = CIHookRegistry()
        reg.register("push", "fail_wf")

        async def failing_executor(wf_name: str, payload: dict[str, Any]) -> dict[str, Any]:
            raise RuntimeError("executor error")

        results = await reg.process_event("push", {}, executor=failing_executor)
        assert results[0]["status"] == "error"
        assert "executor error" in results[0]["error"]

    @pytest.mark.asyncio
    async def test_multiple_hooks_same_event(self) -> None:
        reg = CIHookRegistry()
        reg.register("push", "workflow_a")
        reg.register("push", "workflow_b")

        results = await reg.process_event("push", {})
        assert len(results) == 2
        names = {r["workflow"] for r in results}
        assert names == {"workflow_a", "workflow_b"}

    @pytest.mark.asyncio
    async def test_trigger_log_persists(self) -> None:
        reg = CIHookRegistry()
        reg.register("schedule", "nightly_maintenance")
        await reg.process_event("schedule", {"cron": "0 2 * * *"})
        assert len(reg.trigger_log) == 1

    def test_different_events_isolated(self) -> None:
        reg = CIHookRegistry()
        reg.register("push", "push_wf")
        reg.register("merge", "merge_wf")
        push_hooks = [h for h in reg.hooks if h.event == "push"]
        merge_hooks = [h for h in reg.hooks if h.event == "merge"]
        assert len(push_hooks) == 1
        assert len(merge_hooks) == 1


# ─── Cross-Domain (task 95) ──────────────────────────────────────────────────


class TestDomainRegistry:
    def _make_domain_dir(
        self, tmp_path: Path, name: str, ports: list[dict[str, Any]] | None = None
    ) -> Path:
        d = tmp_path / name
        d.mkdir()
        manifest: dict[str, Any] = {
            "name": name,
            "owner": "test",
            "purpose": "testing",
            "ports": ports or [],
        }
        import yaml
        (d / "domain.yaml").write_text(yaml.dump(manifest), encoding="utf-8")
        return d

    def test_discover_path_finds_domain(self, tmp_path: Path) -> None:
        self._make_domain_dir(tmp_path, "backend")
        reg = DomainRegistry()
        found = reg.discover_path(tmp_path)
        assert "backend" in found

    def test_register_domain_manually(self, tmp_path: Path) -> None:
        reg = DomainRegistry()
        reg.register_domain("mydom", tmp_path / "mydom")
        assert reg.get_domain("mydom") is not None

    def test_cross_domain_port_resolution(self, tmp_path: Path) -> None:
        reg = DomainRegistry()
        reg.register_domain("domA", tmp_path / "a", manifest={
            "ports": [{"name": "output_port", "direction": "output", "schema": "CodeResult"}]
        })
        reg.register_domain("domB", tmp_path / "b", manifest={
            "ports": [{"name": "input_port", "direction": "input", "schema": "CodeResult"}]
        })
        result = reg.resolve_cross_domain_connection("domA.output_port", "domB.input_port")
        assert result["compatible"] is True
        assert result["source_domain"] == "domA"
        assert result["target_domain"] == "domB"

    def test_cross_domain_schema_mismatch(self, tmp_path: Path) -> None:
        reg = DomainRegistry()
        reg.register_domain("domA", tmp_path / "a", manifest={
            "ports": [{"name": "out", "direction": "output", "schema": "CodeResult"}]
        })
        reg.register_domain("domB", tmp_path / "b", manifest={
            "ports": [{"name": "in", "direction": "input", "schema": "ReviewResult"}]
        })
        result = reg.resolve_cross_domain_connection("domA.out", "domB.in")
        assert result["compatible"] is False
        assert "Schema mismatch" in result["error"]

    def test_cross_domain_unknown_source(self, tmp_path: Path) -> None:
        reg = DomainRegistry()
        result = reg.resolve_cross_domain_connection("unknown.port", "domB.in")
        assert result["compatible"] is False
        assert "not found" in result["error"]

    def test_load_from_config(self, tmp_path: Path) -> None:
        import yaml
        # Create a real domain
        self._make_domain_dir(tmp_path, "svc_domain")

        config = {"discovery": {"paths": [str(tmp_path)]}}
        cfg_file = tmp_path / "domains.yaml"
        cfg_file.write_text(yaml.dump(config), encoding="utf-8")

        reg = DomainRegistry()
        reg.load_from_config(cfg_file)
        assert "svc_domain" in reg.domains

    def test_load_from_config_missing_file(self) -> None:
        reg = DomainRegistry()
        reg.load_from_config("/nonexistent/path/domains.yaml")
        assert reg.domains == {}

    def test_port_resolution_returns_none_for_unknown(self) -> None:
        reg = DomainRegistry()
        assert reg.resolve_port("domain.nonexistent") is None


# ─── Maintenance Agents Structure (task 93) ──────────────────────────────────


class TestMaintenanceAgentStructure:
    BASE = Path(__file__).resolve().parents[2] / "agents" / "_maintenance"

    def test_doc_gardener_manifest_exists(self) -> None:
        assert (self.BASE / "DocGardenerAgent" / "v1" / "agent_manifest.yaml").exists()

    def test_doc_gardener_prompt_exists(self) -> None:
        assert (self.BASE / "DocGardenerAgent" / "v1" / "system_prompt.md").exists()

    def test_quality_score_manifest_exists(self) -> None:
        assert (self.BASE / "QualityScoreAgent" / "v1" / "agent_manifest.yaml").exists()

    def test_quality_score_prompt_exists(self) -> None:
        assert (self.BASE / "QualityScoreAgent" / "v1" / "system_prompt.md").exists()

    def test_drift_cleanup_manifest_exists(self) -> None:
        assert (self.BASE / "DriftCleanupAgent" / "v1" / "agent_manifest.yaml").exists()

    def test_drift_cleanup_prompt_exists(self) -> None:
        assert (self.BASE / "DriftCleanupAgent" / "v1" / "system_prompt.md").exists()

    def test_manifests_have_required_fields(self) -> None:
        import yaml
        for agent_name in ("DocGardenerAgent", "QualityScoreAgent", "DriftCleanupAgent"):
            manifest_path = self.BASE / agent_name / "v1" / "agent_manifest.yaml"
            data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            assert "id" in data, f"{agent_name}: missing id"
            assert "system_prompt_ref" in data, f"{agent_name}: missing system_prompt_ref"
            assert "execution_mode" in data, f"{agent_name}: missing execution_mode"


# ─── Maintenance Workflow Structure (task 92) ────────────────────────────────


class TestMaintenanceWorkflowStructure:
    BASE = Path(__file__).resolve().parents[2] / "workflows" / "maintenance"

    def test_doc_gardening_workflow_exists(self) -> None:
        assert (self.BASE / "doc_gardening.yaml").exists()

    def test_quality_scoring_workflow_exists(self) -> None:
        assert (self.BASE / "quality_scoring.yaml").exists()

    def test_drift_cleanup_workflow_exists(self) -> None:
        assert (self.BASE / "drift_cleanup.yaml").exists()

    def test_workflows_have_valid_topology(self) -> None:
        import yaml
        for wf_name in ("doc_gardening", "quality_scoring", "drift_cleanup"):
            wf_path = self.BASE / f"{wf_name}.yaml"
            data = yaml.safe_load(wf_path.read_text(encoding="utf-8"))
            assert "name" in data
            assert "steps" in data
            step_names = {s["name"] for s in data["steps"]}
            for step in data["steps"]:
                for dep in step.get("depends_on", []):
                    assert dep in step_names, f"{wf_name}: step '{step['name']}' depends on unknown '{dep}'"

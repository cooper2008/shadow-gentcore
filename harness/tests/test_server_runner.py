"""Tests for harness/server/runner.py — PRs 2.1, 2.3, 3.3, 3.4.

Covers:
  - PR 2.1: Unique task_id per run_agent call
  - PR 2.3: Error responses sanitised (no filesystem paths leaked)
  - PR 3.3: run_agent accepts both str and dict task input
  - PR 3.4: Broken manifest in list_agents logs a warning instead of silently passing

Design note
-----------
run_agent / run_workflow use *local* imports inside the function body, so
names like AgentRunner, TaskEnvelope etc. are not attributes of the
harness.server.runner module.  We therefore intercept them one level up:
we patch ``harness.server.runner._make_provider``,
``harness.server.runner.ManifestLoader`` (module-level imports that *are*
patched on the runner namespace), and for the deeply-nested local imports
we patch at the ``sys.modules`` level or mock the loader so the internal
logic never reaches the heavy dependencies.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_domain(tmp_path: Path) -> Path:
    """Return a minimal domain directory."""
    (tmp_path / "agents").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _make_agent_dir(domain: Path, name: str = "MyAgent", version: str = "v1") -> Path:
    d = domain / "agents" / name / version
    d.mkdir(parents=True, exist_ok=True)
    return d


def _build_loader_mock(domain: Path, agent_dir: Path):
    """Return a ManifestLoader mock whose load_agent side-effect captures TaskEnvelope."""
    mock = MagicMock()
    mock.load_domain.return_value = MagicMock()
    mock.load_agent.return_value = (MagicMock(), "system-prompt", [])
    return mock


class _FakeTaskEnvelope:
    """Minimal stand-in for TaskEnvelope that records construction kwargs."""

    _instances: list["_FakeTaskEnvelope"] = []

    def __init__(self, *, task_id: str, agent_id: str, input_payload: dict):
        self.task_id = task_id
        self.agent_id = agent_id
        self.input_payload = input_payload
        _FakeTaskEnvelope._instances.append(self)

    @classmethod
    def reset(cls):
        cls._instances.clear()


def _fake_agent_runner_cls(captured_envelopes: list):
    """Factory that returns a class whose run() appends the task envelope."""

    class _FakeAgentRunner:
        def __init__(self, **kwargs):
            pass

        async def run(self, manifest, task, system_prompt_content, context_items):
            captured_envelopes.append(task)
            return {"output": "ok"}

    return _FakeAgentRunner


def _patch_local_imports(captured_envelopes: list):
    """
    Context manager that patches the modules imported locally inside run_agent.

    Because run_agent does ``from harness.core.agent_runner import AgentRunner``
    at call time, we inject fake modules into sys.modules so that the local
    ``from X import Y`` resolves to our mocks.
    """
    fake_agent_runner_mod = types.ModuleType("harness.core.agent_runner")
    fake_agent_runner_mod.AgentRunner = _fake_agent_runner_cls(captured_envelopes)

    fake_tool_executor_mod = types.ModuleType("harness.core.tool_executor")
    fake_tool_executor_mod.ToolExecutor = MagicMock(return_value=MagicMock())

    fake_builtins_mod = types.ModuleType("harness.tools.builtin")
    fake_builtins_mod.register_builtins = MagicMock()

    fake_envelope_mod = types.ModuleType("agent_contracts.contracts.task_envelope")
    fake_envelope_mod.TaskEnvelope = _FakeTaskEnvelope

    # Also ensure parent packages exist in sys.modules to avoid import errors
    mods = {
        "harness.core.agent_runner": fake_agent_runner_mod,
        "harness.core.tool_executor": fake_tool_executor_mod,
        "harness.tools.builtin": fake_builtins_mod,
        "agent_contracts.contracts.task_envelope": fake_envelope_mod,
    }

    originals = {k: sys.modules.get(k) for k in mods}

    class _CM:
        def __enter__(self):
            sys.modules.update(mods)
            return self

        def __exit__(self, *exc):
            for k, v in originals.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v

    return _CM()


# ---------------------------------------------------------------------------
# PR 2.1 — Unique task_id per run_agent call
# ---------------------------------------------------------------------------

class TestUniqueTaskId:
    """Two consecutive run_agent calls must produce different task_ids."""

    def test_two_calls_produce_different_task_ids(self, tmp_path: Path) -> None:
        _FakeTaskEnvelope.reset()
        domain = _make_domain(tmp_path)
        agent_dir = _make_agent_dir(domain)
        loader_mock = _build_loader_mock(domain, agent_dir)
        captured: list = []

        with _patch_local_imports(captured):
            with (
                patch("harness.server.runner._validate_domain_path", return_value=domain),
                patch("harness.server.runner.ManifestLoader", return_value=loader_mock),
                patch("harness.server.runner._make_provider", return_value=MagicMock()),
                patch("harness.server.runner._resolve_agent_dir", return_value=agent_dir),
            ):
                from harness.server import runner as runner_mod

                async def _run():
                    await runner_mod.run_agent("MyAgent/v1", "task", str(domain))
                    await runner_mod.run_agent("MyAgent/v1", "task", str(domain))

                asyncio.run(_run())

        assert len(_FakeTaskEnvelope._instances) == 2
        id1 = _FakeTaskEnvelope._instances[0].task_id
        id2 = _FakeTaskEnvelope._instances[1].task_id
        assert id1 != id2, f"task_ids must differ but both were {id1!r}"

    def test_task_id_contains_agent_id_slug(self, tmp_path: Path) -> None:
        _FakeTaskEnvelope.reset()
        domain = _make_domain(tmp_path)
        agent_dir = _make_agent_dir(domain)
        loader_mock = _build_loader_mock(domain, agent_dir)
        captured: list = []

        with _patch_local_imports(captured):
            with (
                patch("harness.server.runner._validate_domain_path", return_value=domain),
                patch("harness.server.runner.ManifestLoader", return_value=loader_mock),
                patch("harness.server.runner._make_provider", return_value=MagicMock()),
                patch("harness.server.runner._resolve_agent_dir", return_value=agent_dir),
            ):
                from harness.server import runner as runner_mod
                asyncio.run(runner_mod.run_agent("MyAgent/v1", "task", str(domain)))

        assert _FakeTaskEnvelope._instances, "No TaskEnvelope was created"
        task_id = _FakeTaskEnvelope._instances[0].task_id
        assert task_id.startswith("api-MyAgent-v1-"), (
            f"Expected prefix 'api-MyAgent-v1-', got: {task_id!r}"
        )
        # The suffix after the slug should be an 8-char hex string
        suffix = task_id[len("api-MyAgent-v1-"):]
        assert len(suffix) == 8 and all(c in "0123456789abcdef" for c in suffix), (
            f"Suffix {suffix!r} is not an 8-char hex string"
        )


# ---------------------------------------------------------------------------
# PR 2.3 — Error responses must not contain filesystem paths
# ---------------------------------------------------------------------------

class TestSanitisedErrorResponses:
    """Exception messages must not be forwarded to the caller verbatim."""

    def test_run_agent_exception_hides_path(self, tmp_path: Path) -> None:
        domain = _make_domain(tmp_path)
        agent_dir = _make_agent_dir(domain)

        loader_mock = MagicMock()
        loader_mock.load_domain.side_effect = RuntimeError(
            f"Failed reading /etc/secrets and {tmp_path}/sensitive.yaml"
        )

        captured: list = []
        with _patch_local_imports(captured):
            with (
                patch("harness.server.runner._validate_domain_path", return_value=domain),
                patch("harness.server.runner.ManifestLoader", return_value=loader_mock),
                patch("harness.server.runner._make_provider", return_value=MagicMock()),
                patch("harness.server.runner._resolve_agent_dir", return_value=agent_dir),
            ):
                from harness.server import runner as runner_mod
                result = asyncio.run(
                    runner_mod.run_agent("MyAgent/v1", "task", str(domain))
                )

        assert result["status"] == "error"
        assert "/etc/secrets" not in result["error"], "Filesystem path leaked into error"
        assert str(tmp_path) not in result["error"], "tmp_path leaked into error"
        assert result["error"] == "Internal error processing request"
        assert "request_id" in result
        assert len(result["request_id"]) == 8

    def test_run_workflow_exception_hides_path(self, tmp_path: Path) -> None:
        domain = _make_domain(tmp_path)
        wf_file = tmp_path / "workflow.yaml"
        wf_file.write_text("steps: []", encoding="utf-8")

        loader_mock = MagicMock()
        loader_mock.boot_engine.side_effect = RuntimeError(
            f"DB password found in {tmp_path}/secrets"
        )

        with (
            patch("harness.server.runner._validate_domain_path", return_value=domain),
            patch("harness.server.runner.ManifestLoader", return_value=loader_mock),
            patch("harness.server.runner._make_provider", return_value=MagicMock()),
        ):
            from harness.server import runner as runner_mod
            result = asyncio.run(
                runner_mod.run_workflow(str(wf_file), {"key": "val"}, str(domain))
            )

        assert result["status"] == "error"
        assert str(tmp_path) not in result["error"], "tmp_path leaked into error"
        assert result["error"] == "Internal error processing request"
        assert "request_id" in result
        assert len(result["request_id"]) == 8

    def test_run_agent_error_has_unique_request_ids(self, tmp_path: Path) -> None:
        """Each failing request gets its own unique request_id for log correlation."""
        domain = _make_domain(tmp_path)
        agent_dir = _make_agent_dir(domain)

        loader_mock = MagicMock()
        loader_mock.load_domain.side_effect = RuntimeError("boom")

        captured: list = []
        with _patch_local_imports(captured):
            with (
                patch("harness.server.runner._validate_domain_path", return_value=domain),
                patch("harness.server.runner.ManifestLoader", return_value=loader_mock),
                patch("harness.server.runner._make_provider", return_value=MagicMock()),
                patch("harness.server.runner._resolve_agent_dir", return_value=agent_dir),
            ):
                from harness.server import runner as runner_mod

                async def _run():
                    r1 = await runner_mod.run_agent("MyAgent/v1", "t", str(domain))
                    r2 = await runner_mod.run_agent("MyAgent/v1", "t", str(domain))
                    return r1, r2

                r1, r2 = asyncio.run(_run())

        assert r1["request_id"] != r2["request_id"], (
            "Each error response must have a unique request_id"
        )


# ---------------------------------------------------------------------------
# PR 3.3 — run_agent accepts both str and dict task input
# ---------------------------------------------------------------------------

class TestTaskInputNormalisation:
    """run_agent must accept both str and dict[str, Any] for the task parameter."""

    def _run_and_capture_payload(self, domain: Path, task) -> dict:
        """Run run_agent with the given task and return the captured input_payload."""
        _FakeTaskEnvelope.reset()
        agent_dir = _make_agent_dir(domain)
        loader_mock = _build_loader_mock(domain, agent_dir)
        captured: list = []

        with _patch_local_imports(captured):
            with (
                patch("harness.server.runner._validate_domain_path", return_value=domain),
                patch("harness.server.runner.ManifestLoader", return_value=loader_mock),
                patch("harness.server.runner._make_provider", return_value=MagicMock()),
                patch("harness.server.runner._resolve_agent_dir", return_value=agent_dir),
            ):
                from harness.server import runner as runner_mod
                asyncio.run(runner_mod.run_agent("MyAgent/v1", task, str(domain)))

        assert _FakeTaskEnvelope._instances, "No TaskEnvelope was created"
        return _FakeTaskEnvelope._instances[0].input_payload

    def test_str_task_normalised_to_instruction_dict(self, tmp_path: Path) -> None:
        domain = _make_domain(tmp_path)
        payload = self._run_and_capture_payload(domain, "do something")
        assert payload == {"instruction": "do something"}

    def test_dict_task_passed_through_unchanged(self, tmp_path: Path) -> None:
        domain = _make_domain(tmp_path)
        task_dict = {"instruction": "do something", "context": {"key": "value"}}
        payload = self._run_and_capture_payload(domain, task_dict)
        assert payload == task_dict

    def test_dict_with_custom_keys_preserved(self, tmp_path: Path) -> None:
        domain = _make_domain(tmp_path)
        task_dict = {"prompt": "analyse", "mode": "strict", "retries": 3}
        payload = self._run_and_capture_payload(domain, task_dict)
        assert payload == task_dict

    def test_empty_dict_passed_through(self, tmp_path: Path) -> None:
        domain = _make_domain(tmp_path)
        payload = self._run_and_capture_payload(domain, {})
        assert payload == {}


# ---------------------------------------------------------------------------
# PR 3.4 — Broken manifest in list_agents logs a warning
# ---------------------------------------------------------------------------

class TestListAgentsManifestWarning:
    """A broken agent_manifest.yaml must log a warning, not silently pass."""

    def test_broken_manifest_logs_warning(self, tmp_path: Path, caplog) -> None:
        domain = _make_domain(tmp_path)

        # Create a valid manifest
        good_dir = domain / "agents" / "GoodAgent" / "v1"
        good_dir.mkdir(parents=True)
        (good_dir / "agent_manifest.yaml").write_text(
            "id: GoodAgent/v1\ndescription: A good agent\ncategory: test\n",
            encoding="utf-8",
        )

        # Create a broken manifest (invalid YAML)
        bad_dir = domain / "agents" / "BadAgent" / "v1"
        bad_dir.mkdir(parents=True)
        (bad_dir / "agent_manifest.yaml").write_text(
            "id: [unclosed bracket\n\tnot valid yaml: :\n",
            encoding="utf-8",
        )

        with (
            patch("harness.server.runner._validate_domain_path", return_value=domain),
            caplog.at_level(logging.WARNING, logger="harness.server.runner"),
        ):
            from harness.server import runner as runner_mod
            agents = runner_mod.list_agents(str(domain))

        # The good agent still appears
        ids = [a["id"] for a in agents]
        assert any("GoodAgent" in aid for aid in ids), (
            f"Good agent missing from listing: {ids}"
        )

        # A warning was emitted mentioning the bad manifest path
        warning_texts = [r.getMessage() for r in caplog.records
                         if r.levelno == logging.WARNING]
        assert any("BadAgent" in t or "agent_manifest.yaml" in t
                   for t in warning_texts), (
            f"Expected warning about broken manifest, got: {warning_texts}"
        )

    def test_broken_manifest_does_not_raise(self, tmp_path: Path, caplog) -> None:
        """list_agents must return a list even if all manifests are broken."""
        domain = _make_domain(tmp_path)
        bad_dir = domain / "agents" / "BadAgent" / "v1"
        bad_dir.mkdir(parents=True)
        (bad_dir / "agent_manifest.yaml").write_text(
            ": : : invalid\n",
            encoding="utf-8",
        )

        with (
            patch("harness.server.runner._validate_domain_path", return_value=domain),
            caplog.at_level(logging.WARNING, logger="harness.server.runner"),
        ):
            from harness.server import runner as runner_mod
            result = runner_mod.list_agents(str(domain))

        assert isinstance(result, list)
        # Warning should still be emitted
        assert any(r.levelno == logging.WARNING for r in caplog.records)

    def test_no_warning_for_valid_manifest(self, tmp_path: Path, caplog) -> None:
        """No warning is emitted when all manifests parse successfully."""
        domain = _make_domain(tmp_path)
        good_dir = domain / "agents" / "GoodAgent" / "v1"
        good_dir.mkdir(parents=True)
        (good_dir / "agent_manifest.yaml").write_text(
            "id: GoodAgent/v1\ndescription: fine\ncategory: test\n",
            encoding="utf-8",
        )

        with (
            patch("harness.server.runner._validate_domain_path", return_value=domain),
            caplog.at_level(logging.WARNING, logger="harness.server.runner"),
        ):
            from harness.server import runner as runner_mod
            agents = runner_mod.list_agents(str(domain))

        assert len(agents) == 1
        assert not any(r.levelno == logging.WARNING for r in caplog.records), (
            "Unexpected warning for a valid manifest"
        )

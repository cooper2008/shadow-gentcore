"""Tests for build_runtime — the canonical runtime factory.

Three call sites build the runtime today (workflow boot_engine, CLI
``./ai run agent``, and HTTP ``run_agent`` in server/runner.py).
Historically only the workflow path wired RuleEngine into ToolExecutor,
so HTTP and CLI single-agent runs silently bypassed Layer 2-6 rule
enforcement. ``build_runtime`` collapses the three paths into one
construction path. These tests pin that contract:

  1. The factory ALWAYS attaches a RuleEngine (the safety regression
     guard — if someone removes the kwarg in a future refactor, the
     test_rule_engine_always_attached case fails immediately).
  2. Built-in tool adapters are registered by default.
  3. domain_root → MemoryStore wiring works (Tier 4).
  4. omitting domain_root yields no MemoryStore (acceptable for CLI
     dry-runs only).
  5. Callers can pass an existing RuleEngine for cross-bundle sharing.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from harness.core.runtime_factory import RuntimeBundle, build_runtime


class TestBuildRuntime:
    def test_returns_runtime_bundle(self) -> None:
        bundle = build_runtime()
        assert isinstance(bundle, RuntimeBundle)

    def test_rule_engine_always_attached(self) -> None:
        """The single most important safety invariant of this helper."""
        bundle = build_runtime()
        assert bundle.rule_engine is not None
        # tool_executor must hold a reference to the same rule_engine
        assert getattr(bundle.tool_executor, "_rule_engine", None) is bundle.rule_engine \
            or getattr(bundle.tool_executor, "rule_engine", None) is bundle.rule_engine

    def test_builtins_registered_by_default(self) -> None:
        """Common adapters must be present so agents can do basic work."""
        bundle = build_runtime()
        adapters = getattr(bundle.tool_executor, "_adapters", {})
        # A representative subset — file_read is the smoke test.
        assert "file_read" in adapters
        assert "context_retrieve" in adapters
        assert "memory_recall" in adapters

    def test_skip_builtins_when_requested(self) -> None:
        bundle = build_runtime(register_builtin_tools=False)
        adapters = getattr(bundle.tool_executor, "_adapters", {})
        assert "file_read" not in adapters

    def test_no_domain_root_means_no_memory_store(self) -> None:
        bundle = build_runtime()
        assert bundle.memory_store is None

    def test_domain_root_builds_memory_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = build_runtime(Path(tmpdir))
            assert bundle.memory_store is not None

    def test_accepts_str_domain_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = build_runtime(tmpdir)  # str, not Path
            assert bundle.memory_store is not None

    def test_caller_can_share_rule_engine(self) -> None:
        from harness.core.rule_engine import RuleEngine

        shared = RuleEngine()
        b1 = build_runtime(rule_engine=shared)
        b2 = build_runtime(rule_engine=shared)
        assert b1.rule_engine is shared
        assert b2.rule_engine is shared

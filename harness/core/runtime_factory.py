"""Canonical runtime bundle for agent execution.

Three call sites construct the runtime today: the workflow path
(``ManifestLoader.boot_engine``), the CLI single-agent path
(``ai run agent`` in ``harness/cli/ai.py``), and the HTTP single-agent
path (``run_agent`` in ``harness/server/runner.py``). Historically they
diverged: only the workflow path passed a ``RuleEngine`` into
``ToolExecutor``, so a request to the HTTP server's single-agent
endpoint executed under a more permissive policy than the same agent
driven by ``./ai run agent`` or by a workflow step.

``build_runtime`` is the one helper they all should use. It guarantees:

  * ``ToolExecutor`` is constructed with a ``RuleEngine`` (so Layers 2-6
    of rule enforcement actually fire when an agent's manifest opts in
    via ``GENTCORE_ENFORCE_RULES``).
  * Built-in tool adapters are registered (file_read, list_paths,
    context_retrieve, origin_fetch, memory_recall, etc.).
  * A per-domain ``FileMemoryStore`` is built when ``domain_root`` is
    provided — Tier 4 memory is wired automatically.

Callers can pass an existing ``rule_engine`` to share state across
multiple bundles (e.g. when a workflow needs the same hot-reloadable
rule set across all its agent steps).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from harness.core.memory_store import MemoryStore
    from harness.core.rule_engine import RuleEngine
    from harness.core.tool_executor import ToolExecutor


@dataclass
class RuntimeBundle:
    """Canonical runtime components shared by every agent execution path."""

    tool_executor: "ToolExecutor"
    rule_engine: "RuleEngine"
    memory_store: "MemoryStore | None"


def build_runtime(
    domain_root: Path | str | None = None,
    *,
    rule_engine: "RuleEngine | None" = None,
    register_builtin_tools: bool = True,
) -> RuntimeBundle:
    """Build the canonical runtime bundle for any agent execution path.

    Args:
        domain_root: When provided, a per-domain FileMemoryStore is wired.
            When omitted, ``memory_store`` is None (acceptable for ephemeral
            CLI dry-runs but not for production single-agent endpoints).
        rule_engine: Reuse an existing rule engine. When None, a fresh one
            is built — picking up ``config/rules.yaml`` per its loader.
        register_builtin_tools: Skip in tests that want a tool-less
            executor. Default True for every production path.

    Returns:
        A RuntimeBundle with tool_executor, rule_engine, and memory_store.
        ``tool_executor.rule_engine`` is guaranteed non-None.
    """
    from harness.core.rule_engine import RuleEngine
    from harness.core.tool_executor import ToolExecutor

    if rule_engine is None:
        rule_engine = RuleEngine()

    tool_executor = ToolExecutor(rule_engine=rule_engine)

    if register_builtin_tools:
        from harness.tools.builtin import register_builtins
        register_builtins(tool_executor)

    memory_store: Any = None
    if domain_root is not None:
        from harness.core.manifest_loader import build_memory_store
        memory_store = build_memory_store(Path(domain_root))

    return RuntimeBundle(
        tool_executor=tool_executor,
        rule_engine=rule_engine,
        memory_store=memory_store,
    )

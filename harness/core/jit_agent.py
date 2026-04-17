"""JITAgentSynthesizer — experimental runtime synthesis of missing agents (B10).

Audit §5 T5 + §7 row 28: when a workflow references an agent id that
doesn't exist in the registry AND the user has opted in via
`GENTCORE_ALLOW_JIT_AGENT=1` (or the `--allow-jit-agent` CLI flag), the
runtime can synthesise a minimal agent manifest on the fly and register
it, rather than failing. This is the T5 tier of the composition model —
the last-resort escape hatch for novel runtime tasks.

Default behaviour is **disabled** per audit ("Experimental; easy to
defer"). Flag-off, this file is a no-op — the runtime path falls back
to the existing stub-manifest behaviour in ManifestLoader.

Two backends:

  * **stub** (always available) — produces a minimal plan_execute
    manifest with the requested id, a trivial system prompt, and no
    declared tools. Useful for smoke tests + experiments.

  * **factory** (optional) — delegates to a `_factory/AgentFactoryAgent`
    invocation to produce a real CapabilityRecipe-driven agent. Wired
    when the caller passes a `factory_runner` callable.

Both backends return a dict with the same shape: `{manifest, path}`.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable

import yaml

logger = logging.getLogger(__name__)


_ENABLE_ENV = "GENTCORE_ALLOW_JIT_AGENT"


def is_enabled() -> bool:
    """Return True iff the runtime flag opts the user into JIT synthesis.

    Reads from the env var on every call (not cached) so changes take
    effect immediately — matches the pattern used by GENTCORE_ENFORCE_RULES
    (H3) and GENTCORE_ARCHITECT_V2 (B5).
    """
    raw = os.environ.get(_ENABLE_ENV, "").strip().lower()
    return raw in ("1", "true", "yes", "on")


# Signature for the optional AgentFactoryAgent bridge.
@runtime_checkable
class FactoryRunner(Protocol):
    def __call__(self, recipe: dict[str, Any]) -> dict[str, Any]:
        """Run AgentFactoryAgent with a CapabilityRecipe, return agent dict."""
        ...


class JITSynthesisDenied(RuntimeError):
    """Raised when synthesis is requested while the flag is disabled.

    Callers should catch this + fall back to the normal "agent not found"
    error path so failures stay surfaced.
    """


class JITAgentSynthesizer:
    """Factory for runtime-born agents.

    Two modes, selected at construction:

      * stub-mode — no factory runner provided. `synthesize` produces a
        minimal manifest in a scratch dir and returns it. Safe for
        tests + experiments.

      * factory-mode — `factory_runner` is a callable that takes a
        CapabilityRecipe dict and returns an agent dict (manifest +
        system_prompt). `synthesize` delegates to it.

    In both modes the flag `GENTCORE_ALLOW_JIT_AGENT=1` must be truthy,
    OR the synthesizer must be constructed with `force_enabled=True`
    (tests only).
    """

    def __init__(
        self,
        factory_runner: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        scratch_dir: str | Path | None = None,
        force_enabled: bool = False,
    ) -> None:
        self._factory_runner = factory_runner
        self._scratch_dir = (
            Path(scratch_dir) if scratch_dir else Path(tempfile.gettempdir()) / "gentcore_jit_agents"
        )
        self._force_enabled = force_enabled

    def _check_enabled(self) -> None:
        """Gate: flag must be on OR force_enabled set. Raise otherwise."""
        if self._force_enabled:
            return
        if not is_enabled():
            raise JITSynthesisDenied(
                f"JIT agent synthesis disabled. Set {_ENABLE_ENV}=1 to enable."
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def synthesize(
        self,
        agent_id: str,
        goal: str = "",
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Synthesise a runtime agent and return `{manifest, path, mode}`.

        Args:
            agent_id: Target agent identifier, e.g. `domain/NovelAgent/v1`.
            goal: One-line description of what the agent must do. Routed
                into the CapabilityRecipe when using factory-mode.
            context: Optional context for factory-mode synthesis
                (standards, prior outputs, declared tools).

        Returns:
            Dict with keys `{manifest, path, mode}`. `path` is the
            agent directory on disk (containing agent_manifest.yaml).

        Raises:
            JITSynthesisDenied: when the flag is off and force_enabled
                is not set.
        """
        self._check_enabled()
        context = context or {}
        if self._factory_runner is not None:
            return self._synthesize_via_factory(agent_id, goal, context)
        return self._synthesize_stub(agent_id, goal)

    # ------------------------------------------------------------------
    # Stub-mode
    # ------------------------------------------------------------------

    def _synthesize_stub(self, agent_id: str, goal: str) -> dict[str, Any]:
        """Minimal agent — plan_execute, no tools, trivial prompt."""
        parts = agent_id.split("/")
        if len(parts) < 3:
            raise ValueError(f"agent_id must be <domain>/<Name>/<v>, got {agent_id!r}")
        agent_dir = self._scratch_dir / parts[0] / parts[1] / parts[2]
        agent_dir.mkdir(parents=True, exist_ok=True)

        manifest: dict[str, Any] = {
            "id": agent_id,
            "domain": parts[0],
            "category": "reasoning",
            "version": "1.0.0",
            "description": f"JIT-synthesised agent (stub). Goal: {goal or 'unspecified'}",
            "system_prompt_ref": "system_prompt.md",
            "execution_mode": {"primary": "plan_execute", "max_plan_steps": 5},
            "tools": [],
            "permissions": {
                "file_edit": "deny",
                "file_create": "deny",
                "shell_command": "deny",
                "network_access": "deny",
            },
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
            "metadata": {"synthesised_by": "JITAgentSynthesizer", "mode": "stub"},
        }
        (agent_dir / "agent_manifest.yaml").write_text(yaml.dump(manifest), encoding="utf-8")
        (agent_dir / "system_prompt.md").write_text(
            f"# {parts[1]} (JIT stub)\n\n{goal or 'No goal specified.'}\n",
            encoding="utf-8",
        )
        logger.info("JIT stub-synthesised %s at %s", agent_id, agent_dir)
        return {"manifest": manifest, "path": agent_dir, "mode": "stub"}

    # ------------------------------------------------------------------
    # Factory-mode
    # ------------------------------------------------------------------

    def _synthesize_via_factory(
        self,
        agent_id: str,
        goal: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Delegate to AgentFactoryAgent via the caller-provided runner.

        The runner receives a CapabilityRecipe dict and must return
        `{manifest, path}` (or a superset). Errors surface as
        RuntimeError — callers decide whether to fall back to stub mode
        or propagate.
        """
        if self._factory_runner is None:
            raise RuntimeError("factory_runner is not configured")
        recipe: dict[str, Any] = {
            "agent_id": agent_id,
            "goal": goal,
            "context": context,
            "tools": context.get("tools", []),
        }
        result = self._factory_runner(recipe)
        if not isinstance(result, dict) or "manifest" not in result:
            raise RuntimeError(
                f"factory_runner returned invalid payload (missing 'manifest'): {result!r}"
            )
        result.setdefault("mode", "factory")
        logger.info("JIT factory-synthesised %s", agent_id)
        return result

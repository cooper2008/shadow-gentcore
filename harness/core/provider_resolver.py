"""Auto-resolve a provider config for an agent based on its category + creds.

Maps `category:` (or genesis_agent_id) → tier → first model in the tier's
ranked list whose `api_key_env` is present in the environment.

Two integration points:

1. **Builder normalizer** (genesis-time) — when an LLM-emitted agent_manifest
   doesn't carry its own `provider:` block, inject the resolved one. Keeps
   weak-instruct vendors away from code-writing slots they can't handle
   (e.g., MiniMax M2.7 for CodeWriter).

2. **Genesis CLI** (runtime) — `./ai genesis build` resolves per-step
   providers using `genesis_step_tiers` so the architect/builder steps
   always get the strongest tier the user has credentials for, regardless
   of the domain-wide default.

The registry is `config/model_tiers.yaml` at the framework level. Domains
can override by placing their own `model_tiers.yaml` under
`<domain>/config/`; per-key entries shallow-merge over the framework defaults.

Pure functions — no I/O after `load_tiers()`. Easy to unit-test.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


# ────────────────────────────────────────────────────────────────
# Load + merge
# ────────────────────────────────────────────────────────────────


_FRAMEWORK_TIERS_PATH = (
    Path(__file__).resolve().parent.parent.parent / "config" / "model_tiers.yaml"
)


def load_tiers(domain_root: str | Path | None = None) -> dict[str, Any]:
    """Load framework tiers, optionally merged with a domain-local override.

    Args:
        domain_root: Path to the domain dir. If `<domain>/config/model_tiers.yaml`
            exists, it shallow-merges over the framework registry — same shape.

    Returns:
        Dict with keys: ``tiers``, ``category_to_tier``, ``genesis_step_tiers``.
    """
    base: dict[str, Any] = {}
    if _FRAMEWORK_TIERS_PATH.exists():
        try:
            base = yaml.safe_load(_FRAMEWORK_TIERS_PATH.read_text()) or {}
        except Exception:
            base = {}

    if domain_root:
        override_path = Path(domain_root) / "config" / "model_tiers.yaml"
        if override_path.exists():
            try:
                override = yaml.safe_load(override_path.read_text()) or {}
            except Exception:
                override = {}
            base = _shallow_merge(base, override)

    return base


def _shallow_merge(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Per-top-level-key merge: b's values replace a's wholly when both dicts.

    Not deep-merge by design — overriding `tiers.codegen-strong.recommended`
    means "use my list verbatim", not "splice mine in alongside the framework's".
    """
    out = dict(a)
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            inner = dict(out[k])
            inner.update(v)
            out[k] = inner
        else:
            out[k] = v
    return out


# ────────────────────────────────────────────────────────────────
# Resolver
# ────────────────────────────────────────────────────────────────


def resolve_provider_for_agent(
    *,
    agent_id: str | None = None,
    category: str | None = None,
    tiers_doc: dict[str, Any],
    available_creds: set[str] | None = None,
) -> dict[str, Any] | None:
    """Pick the best provider config for an agent.

    Order of precedence:
      1. ``genesis_step_tiers[agent_id]`` — exact match for genesis agents
      2. ``category_to_tier[category]`` — for domain agents
      3. fall back to ``planning-medium``

    Within the chosen tier, walk ``recommended`` in order and return the first
    model whose ``api_key_env`` is present in ``available_creds``.

    Args:
        agent_id: Full agent path like ``_genesis/AgentBuilderAgent/v1``.
            Used for the per-genesis override map. Optional for domain agents.
        category: ``manifest['category']`` value. Required when ``agent_id``
            isn't a genesis-step entry.
        tiers_doc: Output of :func:`load_tiers`.
        available_creds: Set of env-var names currently set. Defaults to
            reading ``os.environ`` — pass explicitly in tests.

    Returns:
        Provider spec dict ready to drop into a manifest's ``provider:``
        block (``{provider, model, api_key_env, base_url?, max_tokens?}``)
        or ``None`` when no recommended model has credentials available.
    """
    if available_creds is None:
        available_creds = set(os.environ.keys())

    tier_name = _pick_tier(agent_id, category, tiers_doc)
    tier_def = (tiers_doc.get("tiers") or {}).get(tier_name) or {}
    recommended = tier_def.get("recommended") or []
    avoid = set(tier_def.get("avoid_models") or [])

    for spec in recommended:
        if not isinstance(spec, dict):
            continue
        model_id = str(spec.get("model") or "")
        if model_id in avoid:
            # Defensive — shouldn't happen if the registry is consistent, but
            # protects users who hand-edit and accidentally list an avoided
            # model in `recommended` too.
            continue
        env_var = spec.get("api_key_env")
        if env_var and env_var in available_creds:
            return _normalize_spec(spec, tier_name)

    return None


def _pick_tier(
    agent_id: str | None,
    category: str | None,
    tiers_doc: dict[str, Any],
) -> str:
    if agent_id:
        # Try exact agent_id match first
        step_map = tiers_doc.get("genesis_step_tiers") or {}
        if agent_id in step_map:
            return str(step_map[agent_id])
        # Try without trailing version (e.g. _genesis/Foo/v2 → _genesis/Foo)
        # in case the registry uses a versionless key.
        bare = agent_id.rsplit("/v", 1)[0]
        for key, val in step_map.items():
            if str(key).rsplit("/v", 1)[0] == bare:
                return str(val)
    cat_map = tiers_doc.get("category_to_tier") or {}
    if category and category in cat_map:
        return str(cat_map[category])
    if category:
        # Soft-match: substring of any registered category
        c_lower = str(category).lower()
        for key, val in cat_map.items():
            if str(key).lower() in c_lower:
                return str(val)
    return "planning-medium"


def _normalize_spec(spec: dict[str, Any], tier_name: str) -> dict[str, Any]:
    """Project a recommended-list entry into a manifest-shaped provider block."""
    out: dict[str, Any] = {
        "provider": spec["provider"],
        "model": spec["model"],
        "api_key_env": spec["api_key_env"],
    }
    if "base_url" in spec:
        out["base_url"] = spec["base_url"]
    if "max_tokens" in spec:
        out["max_tokens"] = spec["max_tokens"]
    # Mark as auto-resolved so humans can spot framework picks vs hand-set
    out["_resolved_tier"] = tier_name
    return out


# ────────────────────────────────────────────────────────────────
# Coverage report
# ────────────────────────────────────────────────────────────────


def coverage_report(
    *, tiers_doc: dict[str, Any], available_creds: set[str] | None = None
) -> dict[str, Any]:
    """Snapshot of which tiers have a covering model given current credentials.

    Used by ``./ai providers status`` to surface gaps before the user runs
    genesis and discovers mid-pipeline that no model supports CodeWriter.

    Returns:
        ``{
            "tiers": {<tier>: {"covered_by": <model> | None,
                                "fallback_only": bool,
                                "missing_envs": [...]}},
            "missing_for_full": [<env_var>, ...],
        }``
    """
    if available_creds is None:
        available_creds = set(os.environ.keys())
    out_tiers: dict[str, Any] = {}
    missing_all: list[str] = []
    for tier_name, tier_def in (tiers_doc.get("tiers") or {}).items():
        avoid = set(tier_def.get("avoid_models") or [])
        covered_by: str | None = None
        missing_envs: list[str] = []
        for spec in tier_def.get("recommended") or []:
            if not isinstance(spec, dict):
                continue
            if str(spec.get("model")) in avoid:
                continue
            env_var = spec.get("api_key_env")
            if env_var and env_var in available_creds:
                if covered_by is None:
                    covered_by = str(spec["model"])
            elif env_var:
                missing_envs.append(env_var)
        out_tiers[tier_name] = {
            "covered_by": covered_by,
            "missing_envs": missing_envs,
        }
        if covered_by is None:
            for e in missing_envs:
                if e not in missing_all:
                    missing_all.append(e)
    return {"tiers": out_tiers, "missing_for_full": missing_all}

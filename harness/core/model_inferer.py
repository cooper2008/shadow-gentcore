"""Infer tier + family for unknown model IDs from naming conventions.

Real problem: ``known_vendors.yaml`` is a hardcoded snapshot. In 3 months
Anthropic ships ``claude-opus-4-7``, OpenAI ships ``gpt-6``, Google ships
``gemini-3.5-flash``. The framework can't auto-route to the strongest model
when it doesn't know the new model exists.

This module bridges the gap. Given a model ID we've never seen:

  1. **Family classifier** — map by substring (claude / gemini-flash /
     gpt / glm / kimi / qwen / deepseek / minimax). Drives prompt nudges
     in :mod:`model_hints`.

  2. **Tier classifier** — map by capability marker in the suffix:
     - ``opus|max|reasoner|coder`` → ``codegen-strong``
     - ``sonnet|pro`` → ``codegen-strong`` for substantive output
     - ``haiku|flash|plus`` → ``planning-medium``
     - ``mini|lite|nano|turbo`` → ``classification-light``
     - ``thinking`` (Gemini 3.x pro/flash thinking) → bumps tier up

The classifier is intentionally **conservative** for codegen-strong: a
new unknown name that doesn't carry a strength marker defaults to
``planning-medium`` (won't accidentally route weak-newer-model to
CodeWriter). The user can override per-domain via
``<domain>/config/model_tiers.yaml``.

This is heuristics, not benchmarks. The framework can't "know" which
model is best — that's a benchmark question that changes every week.
What it CAN do is route based on the published capability tier the
vendor implies via the model name. Auto-discovery surfaces new models;
``./ai providers refresh`` shows the diff so humans can confirm.
"""

from __future__ import annotations

import re
from typing import Any


# ────────────────────────────────────────────────────────────────
# Family classification — drives prompt nudges + provider class
# ────────────────────────────────────────────────────────────────


def infer_family(model_id: str) -> str:
    """Best-effort guess of the model family — for prompt-nudge routing.

    Order matters: most specific patterns first so e.g.
    ``gemini-3-flash-preview`` is classified as ``gemini-flash`` not
    just ``gemini``.
    """
    m = (model_id or "").lower()
    if not m:
        return "unknown"

    # Anthropic
    if "claude" in m:
        return "claude"

    # Google Gemini — split flash vs pro for hint routing
    if "gemini" in m:
        if "flash" in m:
            return "gemini-flash"
        if "pro" in m and ("3" in m or "thinking" in m):
            return "gemini-pro-thinking"
        return "gemini-pro"

    # OpenAI
    if re.match(r"^gpt-?\d", m) or m.startswith("gpt-") or m.startswith("o1") or m.startswith("o3"):
        return "gpt"

    # Chinese vendors — distinct families
    if "glm-" in m:
        return "glm"
    if "minimax" in m or m.startswith("m2") or m == "abab":
        return "minimax"
    if "moonshot" in m or "kimi" in m:
        return "kimi"
    if "qwen" in m:
        return "qwen"
    if "deepseek" in m:
        return "deepseek"

    # OpenRouter `vendor/model` form
    if "/" in m:
        return infer_family(m.split("/", 1)[1])

    return "unknown"


# ────────────────────────────────────────────────────────────────
# Tier classification — drives the resolver's avoid + recommended lists
# ────────────────────────────────────────────────────────────────


# Markers ordered by tier-priority. The classifier walks them in order
# and returns the first match. A model name that contains BOTH "lite"
# and "pro" (e.g. `gemini-3.1-flash-lite-preview`) hits "lite" first
# and gets demoted to classification-light — which is what we want.

# All markers use HYPHEN-ANCHORED matching — must appear as a discrete
# token in the model id, not as a substring inside another token.
# Critical: prevents `mini` from matching inside `geMINI-...`.

_LIGHT_MARKERS = (
    "lite", "nano", "turbo", "haiku-cheap",
)

_HAIKU_FLASH_MARKERS = (
    "haiku", "flash", "plus", "mini",
    # OpenAI mid-tier (full canonical names)
    "gpt-3.5", "gpt-4o-mini", "gpt-5-mini", "gpt-5-nano",
)

_STRONG_MARKERS = (
    "opus", "max",
    "reasoner", "coder",
    "sonnet", "pro",
    "ultra",
    "deepseek-v3", "deepseek-r1",
    "qwen-max", "qwen3-coder",
    # OpenAI flagship
    "gpt-5", "gpt-5.5", "gpt-6", "o1-pro", "o3", "o3-pro",
    "kimi-k2",
    # Anthropic explicit
    "claude-opus", "claude-sonnet",
)


def _token_match(marker: str, model_id_lower: str) -> bool:
    """True if ``marker`` appears as a hyphen-/start-/end-bounded token.

    Matches at start of string, end of string, or surrounded by ``-``
    / ``/`` / ``.``. Prevents `mini` matching `gemini`, `pro` matching
    `proton-3`, etc. Multi-token markers like ``gpt-5-mini`` already
    contain hyphens internally — those are matched as substring (the
    whole compound IS the token).
    """
    if "-" in marker:
        # Compound marker (e.g. `claude-opus`, `gpt-5-mini`). Match as
        # substring; the internal hyphens already provide boundary.
        return marker in model_id_lower
    # Bare-word marker — require boundary on both sides
    boundary = "-/."
    n = len(marker)
    idx = 0
    while idx < len(model_id_lower):
        pos = model_id_lower.find(marker, idx)
        if pos < 0:
            return False
        before_ok = pos == 0 or model_id_lower[pos - 1] in boundary
        after_idx = pos + n
        after_ok = after_idx == len(model_id_lower) or model_id_lower[after_idx] in boundary
        if before_ok and after_ok:
            return True
        idx = pos + 1
    return False


def infer_tier(model_id: str) -> str:
    """Best-effort tier classification.

    Priority:
      1. Light markers (lite/nano/turbo) → ``classification-light``
      2. Haiku/Flash/Plus markers → ``planning-medium``
      3. Strong markers (opus/sonnet/pro/coder) → ``codegen-strong``
      4. Otherwise → ``planning-medium`` (conservative default)

    Notable conservative bias: codegen-strong is the most expensive
    tier and the one that triggers cost. Unknown new model? Default
    to medium. The user can promote by editing the registry.
    """
    m = (model_id or "").lower()
    if not m:
        return "planning-medium"

    # Light markers FIRST — `*-flash-lite-*` should beat `*-flash-*`
    for mark in _LIGHT_MARKERS:
        if _token_match(mark, m):
            return "classification-light"

    # Mid-tier markers BEFORE strong markers — `gpt-5-mini` should be
    # mid even though it contains `gpt-5`. Size modifier wins over
    # family marker. Token-boundary matching prevents `mini` from
    # firing inside `geMINI-...`.
    for mark in _HAIKU_FLASH_MARKERS:
        if _token_match(mark, m):
            return "planning-medium"

    # Strong markers last
    for mark in _STRONG_MARKERS:
        if _token_match(mark, m):
            return "codegen-strong"

    return "planning-medium"


# ────────────────────────────────────────────────────────────────
# Compose into a recommended-spec for an unknown model
# ────────────────────────────────────────────────────────────────


def infer_recommended_spec(
    *, model_id: str, vendor: dict[str, Any]
) -> dict[str, Any]:
    """Build a resolver-compatible spec for an unknown model on a known vendor.

    Args:
        model_id: e.g. ``claude-opus-4-7``, ``gpt-6``
        vendor: the matching entry from ``config/known_vendors.yaml``
            (carries ``provider_class``, ``base_url``, ``env_vars``).

    Returns:
        Spec dict with the inferred ``tier_hint`` + ``family`` so callers
        can drop it into ``model_tiers.yaml`` or merge into the live
        registry via :func:`provider_detector.merge_detected_into_tiers`.
    """
    spec: dict[str, Any] = {
        "provider": vendor.get("provider_class", "openai"),
        "model": model_id,
        "api_key_env": (vendor.get("env_vars") or [""])[0],
        "_tier_hint": infer_tier(model_id),
        "_family": infer_family(model_id),
        "_inferred": True,
    }
    if vendor.get("base_url"):
        spec["base_url"] = vendor["base_url"]
    return spec


def diff_known_vs_live(
    *, known: list[str], live: list[str]
) -> dict[str, list[str]]:
    """Compute new / removed / unchanged model IDs.

    Used by ``./ai providers refresh`` to show what's changed since the
    registry was authored. ``new`` are candidates for auto-classification.
    ``removed`` are vendors deprecating a model (rare but happens).
    """
    known_set = {str(m) for m in known}
    live_set = {str(m) for m in live}
    return {
        "new": sorted(live_set - known_set),
        "removed": sorted(known_set - live_set),
        "unchanged": sorted(known_set & live_set),
    }

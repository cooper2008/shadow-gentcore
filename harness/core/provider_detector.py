"""Detect available LLM providers from environment + known-vendor registry.

Walks ``config/known_vendors.yaml``, returns the subset whose credentials
look valid in the current environment. Fed into :mod:`provider_resolver`
so a user gets accurate "which tiers are covered" reports without having
to enumerate their wiring by hand.

Detection is layered:

1. **Static** (always run) — env-var presence + key-pattern regex
   disambiguation. No network. Fast.
2. **Live** (opt-in via ``verify=True``) — short HTTP call to each
   vendor's ``list_models_url`` to confirm the key actually authenticates.
   Used by ``./ai providers detect --live``.

Detected vendors are projected into provider_resolver-compatible specs and
optionally merged into the active tier registry so newly-detected models
flow through the same resolver pipeline as the framework defaults.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_REGISTRY_PATH = (
    Path(__file__).resolve().parent.parent.parent / "config" / "known_vendors.yaml"
)


@dataclass
class DetectedVendor:
    vendor: str
    description: str
    env_vars: list[str]
    provider_class: str
    base_url: str | None
    models: list[dict[str, Any]]
    list_models_url: str | None = None
    notes: str = ""
    # Populated only when verify=True
    live_verified: bool | None = None
    live_error: str | None = None
    live_models_count: int | None = None
    # Populated when the live catalog is fetched + diffed (refresh=True)
    live_models: list[str] = field(default_factory=list)
    new_models: list[dict[str, Any]] = field(default_factory=list)
    removed_models: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


# ────────────────────────────────────────────────────────────────
# Registry load
# ────────────────────────────────────────────────────────────────


def load_known_vendors() -> list[dict[str, Any]]:
    if not _REGISTRY_PATH.exists():
        return []
    try:
        doc = yaml.safe_load(_REGISTRY_PATH.read_text()) or {}
    except Exception:
        return []
    return list(doc.get("vendors") or [])


# ────────────────────────────────────────────────────────────────
# Static detection
# ────────────────────────────────────────────────────────────────


def detect_vendors(
    *, env: dict[str, str] | None = None, verify: bool = False
) -> list[DetectedVendor]:
    """Return list of vendors whose credentials are present (and optionally
    verified live).

    Args:
        env: Mapping to use instead of ``os.environ`` (for tests + sandboxed
            previews — never logs the values).
        verify: When True, hit each vendor's ``list_models_url`` to confirm
            the key authenticates. Adds ~1-2s per detected vendor.

    Returns:
        Sorted list of DetectedVendor records. Anti-pattern disqualifiers
        (e.g. ``sk-or-`` on OPENAI_API_KEY) drop the vendor before the
        match list. Order: direct vendors first, then aggregators, then
        cloud-mediated, then generic OpenAI-key — matches registry order.
    """
    if env is None:
        env = dict(os.environ)
    out: list[DetectedVendor] = []
    for v in load_known_vendors():
        if not _vendor_credentials_present(v, env):
            continue
        if not _key_patterns_match(v, env):
            continue
        # OPENAI_BASE_URL set means OPENAI_API_KEY is routing somewhere
        # else; openai-direct shouldn't claim it.
        if v.get("vendor") == "openai-direct" and env.get("OPENAI_BASE_URL"):
            continue
        detected = DetectedVendor(
            vendor=str(v.get("vendor")),
            description=str(v.get("description") or ""),
            env_vars=list(v.get("env_vars") or []),
            provider_class=str(v.get("provider_class") or "openai"),
            base_url=v.get("base_url"),
            models=list(v.get("models") or []),
            list_models_url=v.get("list_models_url"),
            notes=str(v.get("notes") or ""),
        )
        if verify and detected.list_models_url:
            ok, err, count = _verify_live(detected, env)
            detected.live_verified = ok
            detected.live_error = err
            detected.live_models_count = count
        out.append(detected)
    return out


def _vendor_credentials_present(vendor: dict[str, Any], env: dict[str, str]) -> bool:
    for var in vendor.get("env_vars") or []:
        val = env.get(str(var))
        if not val:
            return False
    return True


def _key_patterns_match(vendor: dict[str, Any], env: dict[str, str]) -> bool:
    """Run the optional regex sanity-check on the FIRST env var's value.

    `key_pattern` (must match) and `key_anti_patterns` (must NOT match)
    disambiguate when the same env var holds different vendors' keys
    (notably OPENAI_API_KEY vs sk-or- OpenRouter keys).
    """
    env_vars = vendor.get("env_vars") or []
    if not env_vars:
        return True
    primary = env.get(str(env_vars[0]), "")
    pattern = vendor.get("key_pattern")
    if pattern and not re.search(str(pattern), primary):
        return False
    for anti in vendor.get("key_anti_patterns") or []:
        if re.search(str(anti), primary):
            return False
    return True


# ────────────────────────────────────────────────────────────────
# Live verification
# ────────────────────────────────────────────────────────────────


def _verify_live(
    vendor: DetectedVendor, env: dict[str, str], timeout: float = 5.0
) -> tuple[bool, str | None, int | None]:
    """GET the vendor's list_models endpoint, return (ok, error, count).

    Uses urllib (stdlib only) to avoid pulling httpx into hot path.
    """
    if not vendor.list_models_url:
        return True, None, None
    url = vendor.list_models_url
    primary_env = vendor.env_vars[0] if vendor.env_vars else None
    key_in_url = primary_env and "${KEY}" in url
    if primary_env and key_in_url:
        url = url.replace("${KEY}", env.get(primary_env, ""))
    headers: dict[str, str] = {}
    # When the URL self-authenticates via `key=` query param (Google's
    # /v1beta/models endpoint), DO NOT also send a Bearer header — some
    # providers reject the duplicate-auth case as 401.
    if not key_in_url:
        if vendor.vendor.startswith("anthropic"):
            headers["x-api-key"] = env.get(primary_env, "") if primary_env else ""
            headers["anthropic-version"] = "2023-06-01"
        else:
            headers["Authorization"] = f"Bearer {env.get(primary_env, '') if primary_env else ''}"

    try:
        import urllib.request
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status >= 400:
                return False, f"HTTP {resp.status}", None
            body = resp.read(64 * 1024).decode("utf-8", errors="replace")
        # Crude count — every list endpoint we know returns a JSON array
        # under either `data:` (OpenAI-shaped) or `models:` (Google/Anthropic).
        count = body.count('"id"') + body.count('"name"')
        return True, None, count or None
    except Exception as exc:
        return False, str(exc)[:200], None


# ────────────────────────────────────────────────────────────────
# Project to resolver-compatible specs
# ────────────────────────────────────────────────────────────────


def refresh_models(
    *, env: dict[str, str] | None = None, timeout: float = 8.0
) -> list[DetectedVendor]:
    """Fetch live model catalogs from each detected vendor and diff against
    the registry's known list. Newly-discovered models get auto-classified
    via :mod:`model_inferer` so they're routable through the resolver
    immediately, before the user manually edits ``known_vendors.yaml``.

    Used by ``./ai providers refresh`` to surface staleness — when Anthropic
    ships claude-opus-4-7 in three months, this is what tells the user.

    For each vendor:
      • static-detect (env vars + key patterns)
      • if list_models_url set → GET it, parse model IDs
      • diff live IDs against ``known_vendors.yaml`` ``models[]``
      • for each NEW id → call :func:`model_inferer.infer_recommended_spec`
        to get a tier-classified spec (with ``_inferred: True`` flag)

    Returns:
        Same shape as :func:`detect_vendors`, but each record carries
        ``live_models`` (full live catalog), ``new_models`` (auto-classified
        candidates for registry promotion), and ``removed_models`` (models
        that disappeared from the vendor's published list).
    """
    if env is None:
        env = dict(os.environ)
    detected = detect_vendors(env=env, verify=False)
    for v in detected:
        if not v.list_models_url:
            continue
        live_ids = _fetch_live_model_ids(v, env, timeout=timeout)
        if live_ids is None:
            v.live_error = "fetch failed"
            continue
        v.live_verified = True
        v.live_models = live_ids
        v.live_models_count = len(live_ids)
        known_ids = [str(m.get("model")) for m in v.models if isinstance(m, dict)]
        from harness.core.model_inferer import diff_known_vs_live, infer_recommended_spec
        diff = diff_known_vs_live(known=known_ids, live=live_ids)
        v.removed_models = diff["removed"]
        # Build vendor dict shape for the inferer
        vendor_for_inferer = {
            "provider_class": v.provider_class,
            "base_url": v.base_url,
            "env_vars": v.env_vars,
        }
        v.new_models = [
            infer_recommended_spec(model_id=mid, vendor=vendor_for_inferer)
            for mid in diff["new"]
        ]
    return detected


def _fetch_live_model_ids(
    vendor: DetectedVendor, env: dict[str, str], timeout: float
) -> list[str] | None:
    """GET the vendor's list_models_url, return list of model IDs.

    Handles two response shapes the registry expects:
      • OpenAI-shaped: ``{"data": [{"id": "..."}, ...]}``
      • Google-shaped: ``{"models": [{"name": "models/..."}, ...]}``
    Returns ``None`` on any failure (caller surfaces as live_error).
    """
    if not vendor.list_models_url or not vendor.env_vars:
        return None
    url = vendor.list_models_url
    primary_env = vendor.env_vars[0]
    key_in_url = "${KEY}" in url
    if key_in_url:
        url = url.replace("${KEY}", env.get(primary_env, ""))
    headers: dict[str, str] = {}
    if not key_in_url:
        if vendor.vendor.startswith("anthropic"):
            headers["x-api-key"] = env.get(primary_env, "")
            headers["anthropic-version"] = "2023-06-01"
        else:
            headers["Authorization"] = f"Bearer {env.get(primary_env, '')}"
    try:
        import json as _json
        import urllib.request
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status >= 400:
                return None
            body = _json.loads(resp.read().decode("utf-8", errors="replace"))
        ids: list[str] = []
        # OpenAI / Anthropic / Moonshot / DeepSeek / OpenRouter shape
        if isinstance(body, dict) and isinstance(body.get("data"), list):
            for item in body["data"]:
                if isinstance(item, dict) and item.get("id"):
                    ids.append(str(item["id"]))
        # Google shape
        elif isinstance(body, dict) and isinstance(body.get("models"), list):
            for item in body["models"]:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "")
                # Google returns `models/<id>` — strip the prefix
                ids.append(name.split("/", 1)[-1] if "/" in name else name)
        return ids
    except Exception:
        return None


def detected_vendors_to_recommended_specs(
    detected: list[DetectedVendor],
) -> dict[str, list[dict[str, Any]]]:
    """Group detected vendors' models by tier_hint into resolver `recommended` lists.

    Returns ``{tier_name: [provider_spec, ...]}``. Each spec is shaped exactly
    like ``model_tiers.yaml`` `recommended[]` entries — drop them straight
    into the resolver's tier doc.
    """
    out: dict[str, list[dict[str, Any]]] = {}
    for v in detected:
        for m in v.models:
            tier = str(m.get("tier_hint") or "planning-medium")
            spec: dict[str, Any] = {
                "provider": v.provider_class,
                "model": m["model"],
                "api_key_env": v.env_vars[0] if v.env_vars else "",
            }
            if v.base_url:
                spec["base_url"] = v.base_url
            spec["_detected_vendor"] = v.vendor
            out.setdefault(tier, []).append(spec)
    return out


def merge_detected_into_tiers(
    tiers_doc: dict[str, Any], detected: list[DetectedVendor]
) -> dict[str, Any]:
    """Append detected models to the END of each tier's `recommended` list.

    Detected vendors come AFTER framework defaults so a curated
    model_tiers.yaml ordering still wins. Useful when a user has a vendor
    set up that the framework doesn't list — they get auto-routing without
    editing the registry.

    Existing entries with the same `model` are deduplicated (first occurrence
    kept — gives the framework's curated entry priority over the detector's).
    """
    out = dict(tiers_doc)
    out["tiers"] = dict(out.get("tiers") or {})
    grouped = detected_vendors_to_recommended_specs(detected)
    for tier_name, new_specs in grouped.items():
        tier_def = dict(out["tiers"].get(tier_name) or {"recommended": [], "avoid_models": []})
        existing = list(tier_def.get("recommended") or [])
        seen_models = {str(s.get("model")) for s in existing if isinstance(s, dict)}
        for spec in new_specs:
            if str(spec.get("model")) in seen_models:
                continue
            existing.append(spec)
            seen_models.add(str(spec.get("model")))
        tier_def["recommended"] = existing
        out["tiers"][tier_name] = tier_def
    return out

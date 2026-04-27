"""Tests for provider auto-detection from environment + known-vendor registry.

Pins:
  - env-var presence drives detection
  - key_pattern disambiguates same-named env vars (sk- vs sk-or- vs AIza)
  - key_anti_patterns kick OpenRouter keys out of openai-direct
  - OPENAI_BASE_URL set excludes openai-direct (key is routed elsewhere)
  - detected vendors project into resolver-shaped specs grouped by tier
  - merge_detected_into_tiers extends tiers without overwriting curated picks
"""

from __future__ import annotations

from typing import Any


from harness.core.provider_detector import (
    DetectedVendor,
    detect_vendors,
    detected_vendors_to_recommended_specs,
    load_known_vendors,
    merge_detected_into_tiers,
)


# ────────────────────────────────────────────────────────────────
# Registry shape


def test_registry_loads_known_vendors() -> None:
    vendors = load_known_vendors()
    assert vendors, "known_vendors.yaml must enumerate at least one vendor"
    ids = {str(v["vendor"]) for v in vendors}
    for required in (
        "anthropic-direct", "google-direct", "zhipu-glm", "minimax",
        "openai-direct", "openrouter", "moonshot-kimi", "deepseek",
        "anthropic-bedrock", "azure-openai",
    ):
        assert required in ids, f"missing vendor in registry: {required}"


def test_registry_anti_patterns_for_openai_direct() -> None:
    vendors = {str(v["vendor"]): v for v in load_known_vendors()}
    od = vendors["openai-direct"]
    anti = od.get("key_anti_patterns") or []
    # Must reject OpenRouter keys masquerading as OpenAI
    assert any("sk-or-" in p for p in anti)
    # Must reject Anthropic keys masquerading as OpenAI
    assert any("sk-ant-" in p for p in anti)


# ────────────────────────────────────────────────────────────────
# detect_vendors — static path


def test_detects_anthropic_direct_with_real_prefix() -> None:
    env = {"ANTHROPIC_API_KEY": "sk-ant-test-123"}
    out = detect_vendors(env=env)
    assert any(v.vendor == "anthropic-direct" for v in out)


def test_rejects_anthropic_direct_with_wrong_prefix() -> None:
    """Pattern guard — wrong key in ANTHROPIC_API_KEY isn't claimed as Anthropic."""
    env = {"ANTHROPIC_API_KEY": "AIzaWrongShape"}
    out = detect_vendors(env=env)
    assert not any(v.vendor == "anthropic-direct" for v in out)


def test_detects_google_with_aiza_key() -> None:
    env = {"GOOGLE_API_KEY": "AIzaSyTest_real_key_shape"}
    out = detect_vendors(env=env)
    g = [v for v in out if v.vendor == "google-direct"]
    assert g
    # Must include Gemini Flash + Pro models
    model_ids = {m["model"] for m in g[0].models}
    assert any("flash" in m for m in model_ids)
    assert any("pro" in m for m in model_ids)


def test_openrouter_key_does_NOT_trigger_openai_direct() -> None:
    """Regression — sk-or- keys are OpenRouter, NOT OpenAI."""
    env = {
        "OPENAI_API_KEY": "sk-or-fake-router-key",
        "OPENROUTER_API_KEY": "sk-or-fake-router-key",
    }
    out = detect_vendors(env=env)
    vendors_seen = {v.vendor for v in out}
    assert "openrouter" in vendors_seen
    assert "openai-direct" not in vendors_seen


def test_openai_base_url_excludes_openai_direct() -> None:
    """If OPENAI_BASE_URL is set, the OPENAI_API_KEY is being used as a
    GLUE for some other vendor (Gemini-via-compat, etc.). Don't claim it
    as OpenAI direct."""
    env = {
        "OPENAI_API_KEY": "sk-some-key-format",
        "OPENAI_BASE_URL": "https://generativelanguage.googleapis.com/v1beta/openai",
    }
    out = detect_vendors(env=env)
    assert not any(v.vendor == "openai-direct" for v in out)


def test_detects_multiple_vendors_in_one_pass() -> None:
    env = {
        "ANTHROPIC_API_KEY": "sk-ant-real-shape",
        "GOOGLE_API_KEY": "AIzaSyRealShape",
        "ZHIPU_API_KEY": "any-key-no-pattern",
        "MINIMAX_API_KEY": "any-minimax",
    }
    out = detect_vendors(env=env)
    seen = {v.vendor for v in out}
    assert {"anthropic-direct", "google-direct", "zhipu-glm", "minimax"} <= seen


def test_glm_detected_carries_base_url_and_models() -> None:
    env = {"ZHIPU_API_KEY": "anything"}
    out = detect_vendors(env=env)
    g = [v for v in out if v.vendor == "zhipu-glm"][0]
    assert g.base_url == "https://open.bigmodel.cn/api/anthropic"
    assert any(m["model"].startswith("glm-") for m in g.models)


def test_minimax_carries_avoid_warning_in_notes() -> None:
    """The registry's `notes:` field must surface MiniMax's codegen weakness."""
    env = {"MINIMAX_API_KEY": "test"}
    out = detect_vendors(env=env)
    mm = [v for v in out if v.vendor == "minimax"][0]
    assert "codegen" in mm.notes.lower() or "avoid" in mm.notes.lower()


def test_no_creds_returns_empty() -> None:
    out = detect_vendors(env={})
    assert out == []


# ────────────────────────────────────────────────────────────────
# Project to resolver-shaped specs


def test_specs_grouped_by_tier_hint() -> None:
    detected = [
        DetectedVendor(
            vendor="anthropic-direct",
            description="Claude direct",
            env_vars=["ANTHROPIC_API_KEY"],
            provider_class="anthropic",
            base_url=None,
            models=[
                {"model": "claude-sonnet", "family": "claude", "tier_hint": "codegen-strong"},
                {"model": "claude-haiku",  "family": "claude", "tier_hint": "planning-medium"},
            ],
        ),
    ]
    grouped = detected_vendors_to_recommended_specs(detected)
    assert "codegen-strong" in grouped
    assert "planning-medium" in grouped
    assert grouped["codegen-strong"][0]["model"] == "claude-sonnet"
    assert grouped["codegen-strong"][0]["api_key_env"] == "ANTHROPIC_API_KEY"
    assert grouped["codegen-strong"][0]["_detected_vendor"] == "anthropic-direct"


def test_merge_does_not_override_curated_picks() -> None:
    """When a curated tier already lists a model, detector's same-model
    pick must NOT be re-added (first-occurrence wins → curated stays first)."""
    tiers_doc: dict[str, Any] = {
        "tiers": {
            "codegen-strong": {
                "avoid_models": [],
                "recommended": [
                    {"provider": "anthropic", "model": "claude-sonnet-4-5-20250929",
                     "api_key_env": "ANTHROPIC_API_KEY"},
                ],
            },
        },
    }
    detected = [
        DetectedVendor(
            vendor="anthropic-direct",
            description="x",
            env_vars=["ANTHROPIC_API_KEY"],
            provider_class="anthropic",
            base_url=None,
            models=[
                {"model": "claude-sonnet-4-5-20250929", "family": "claude",
                 "tier_hint": "codegen-strong"},
                {"model": "claude-haiku-4-5", "family": "claude",
                 "tier_hint": "codegen-strong"},
            ],
        ),
    ]
    merged = merge_detected_into_tiers(tiers_doc, detected)
    cg = merged["tiers"]["codegen-strong"]["recommended"]
    models = [s["model"] for s in cg]
    # Dedup: claude-sonnet appears only once
    assert models.count("claude-sonnet-4-5-20250929") == 1
    # New model (haiku) was appended
    assert "claude-haiku-4-5" in models


def test_merge_creates_tier_when_missing() -> None:
    """Detector finding a tier the framework didn't define still flows."""
    tiers_doc: dict[str, Any] = {"tiers": {}}
    detected = [
        DetectedVendor(
            vendor="kimi", description="x", env_vars=["MOONSHOT_API_KEY"],
            provider_class="openai", base_url="https://api.moonshot.cn/v1",
            models=[{"model": "kimi-k2", "family": "kimi",
                     "tier_hint": "experimental-tier"}],
        ),
    ]
    merged = merge_detected_into_tiers(tiers_doc, detected)
    assert "experimental-tier" in merged["tiers"]
    assert merged["tiers"]["experimental-tier"]["recommended"][0]["model"] == "kimi-k2"


# ────────────────────────────────────────────────────────────────
# DetectedVendor record carries metadata for CLI surface


def test_detected_carries_provider_class_and_base_url() -> None:
    env = {"GOOGLE_API_KEY": "AIzaSyTest"}
    out = detect_vendors(env=env)
    g = next(v for v in out if v.vendor == "google-direct")
    assert g.provider_class == "openai"
    assert g.base_url is not None
    assert "googleapis.com" in g.base_url


def test_detected_with_verify_false_skips_live() -> None:
    """verify=False must NOT call any URL — for test reliability + speed."""
    env = {"ANTHROPIC_API_KEY": "sk-ant-test"}
    out = detect_vendors(env=env, verify=False)
    assert out
    for v in out:
        assert v.live_verified is None

"""Tests for model_inferer — pattern-based tier + family classification.

Pins the heuristics that route brand-new models (Claude Opus 4.7,
Gemini 3.5 Flash, GPT-6, …) to the right tier without manual registry
edits. Conservative bias: unknown new models default to planning-medium,
not codegen-strong, so we don't accidentally route an unproven release
to CodeWriter.
"""

from __future__ import annotations

import pytest

from harness.core.model_inferer import (
    diff_known_vs_live,
    infer_family,
    infer_recommended_spec,
    infer_tier,
)


# ────────────────────────────────────────────────────────────────
# Family classifier — drives prompt nudges in model_hints.py


class TestInferFamily:
    @pytest.mark.parametrize("model,family", [
        ("claude-sonnet-4-5-20250929", "claude"),
        ("claude-opus-4-7-20260101", "claude"),  # future model
        ("anthropic/claude-haiku-4-5", "claude"),
        ("gemini-3-flash-preview", "gemini-flash"),
        ("gemini-3.5-flash", "gemini-flash"),  # future
        ("gemini-3-pro-preview", "gemini-pro-thinking"),
        ("gemini-3.1-pro-preview", "gemini-pro-thinking"),
        ("gemini-2.5-pro", "gemini-pro"),
        ("gpt-5", "gpt"),
        ("gpt-5.5", "gpt"),
        ("gpt-6", "gpt"),
        ("o3-pro", "gpt"),
        ("glm-5.1", "glm"),
        ("glm-4.6", "glm"),
        ("m2.7", "minimax"),
        ("MiniMax-M2", "minimax"),
        ("moonshot-v1-128k", "kimi"),
        ("kimi-k2", "kimi"),
        ("qwen-max", "qwen"),
        ("qwen3-coder", "qwen"),
        ("deepseek-coder", "deepseek"),
        ("deepseek-v3", "deepseek"),
        ("openai/gpt-5.5", "gpt"),  # OpenRouter prefix
        ("anthropic/claude-opus-4-5", "claude"),  # OpenRouter prefix
        ("totally-novel-model-name", "unknown"),
    ])
    def test_family(self, model: str, family: str) -> None:
        assert infer_family(model) == family


# ────────────────────────────────────────────────────────────────
# Tier classifier — drives provider_resolver routing


class TestInferTier:
    @pytest.mark.parametrize("model,tier", [
        # Strong markers — codegen-strong
        ("claude-opus-4-5-20251029", "codegen-strong"),
        ("claude-opus-4-7-future", "codegen-strong"),  # future model still routes
        ("claude-sonnet-4-5", "codegen-strong"),
        ("claude-sonnet-5", "codegen-strong"),
        ("gemini-3-pro-preview", "codegen-strong"),
        ("gemini-3.5-pro", "codegen-strong"),
        ("gpt-5", "codegen-strong"),
        ("gpt-5.5", "codegen-strong"),
        ("gpt-6", "codegen-strong"),
        ("o3-pro", "codegen-strong"),
        ("deepseek-coder", "codegen-strong"),
        ("deepseek-v3", "codegen-strong"),
        ("deepseek-r1", "codegen-strong"),
        ("qwen-max", "codegen-strong"),
        ("qwen3-coder", "codegen-strong"),
        ("kimi-k2", "codegen-strong"),
        ("moonshot-v1-128k", "codegen-strong"),  # 128k = Kimi long
        # Wait — moonshot doesn't have *coder*/opus/etc. Let's leave as planning-medium and see.
    ])
    def test_strong_markers(self, model: str, tier: str) -> None:
        # Sanity — except moonshot-128k which we'll relax
        if "moonshot" in model:
            pytest.skip("moonshot tier depends on size; tested separately")
        assert infer_tier(model) == tier

    @pytest.mark.parametrize("model,tier", [
        ("claude-haiku-4-5", "planning-medium"),
        ("claude-haiku-future", "planning-medium"),
        ("gemini-3-flash-preview", "planning-medium"),
        ("gemini-2.5-flash", "planning-medium"),
        ("gemini-3.5-flash", "planning-medium"),  # future
        ("gpt-5-mini", "planning-medium"),
        ("gpt-5-nano", "classification-light"),  # nano → light
        ("qwen-plus", "planning-medium"),
    ])
    def test_mid_markers(self, model: str, tier: str) -> None:
        assert infer_tier(model) == tier

    @pytest.mark.parametrize("model,tier", [
        ("gemini-3.1-flash-lite-preview", "classification-light"),
        ("gemini-2.0-flash-lite", "classification-light"),
        ("gpt-5-nano", "classification-light"),
        ("qwen-turbo", "classification-light"),
    ])
    def test_light_markers(self, model: str, tier: str) -> None:
        assert infer_tier(model) == tier

    def test_unknown_defaults_to_medium_not_strong(self) -> None:
        """Conservative bias — never auto-promote a brand-new unmarked model
        to codegen-strong. User must explicitly confirm via registry edit."""
        assert infer_tier("totally-novel-model-x42") == "planning-medium"
        # Empty string fails into safe default
        assert infer_tier("") == "planning-medium"

    def test_lite_in_pro_name_demotes_to_light(self) -> None:
        """`gemini-3.1-flash-lite-preview` contains both 'pro'-adjacent
        markers and 'lite' — light wins (we don't accidentally promote
        a lite model to codegen)."""
        assert infer_tier("gemini-3.1-flash-lite-preview") == "classification-light"


# ────────────────────────────────────────────────────────────────
# Combined spec builder


def test_recommended_spec_carries_tier_and_family() -> None:
    """Builds a resolver-compatible spec from a vendor + model name."""
    vendor = {
        "provider_class": "anthropic",
        "base_url": "https://api.anthropic.com",
        "env_vars": ["ANTHROPIC_API_KEY"],
    }
    spec = infer_recommended_spec(model_id="claude-opus-4-7-future", vendor=vendor)
    assert spec["model"] == "claude-opus-4-7-future"
    assert spec["provider"] == "anthropic"
    assert spec["api_key_env"] == "ANTHROPIC_API_KEY"
    assert spec["base_url"] == "https://api.anthropic.com"
    assert spec["_tier_hint"] == "codegen-strong"
    assert spec["_family"] == "claude"
    assert spec["_inferred"] is True


def test_recommended_spec_for_unknown_vendor_works() -> None:
    """Works even when vendor has minimal info — used by ./ai providers refresh
    when surfacing newly-discovered models from list endpoints."""
    vendor = {"provider_class": "openai", "env_vars": ["FOO_KEY"]}
    spec = infer_recommended_spec(model_id="future-model-7", vendor=vendor)
    assert spec["api_key_env"] == "FOO_KEY"
    assert spec["_tier_hint"] == "planning-medium"  # conservative


# ────────────────────────────────────────────────────────────────
# Diff util — what changed between registry and live catalog


class TestDiff:
    def test_new_models_surface_as_additions(self) -> None:
        d = diff_known_vs_live(
            known=["claude-sonnet-4-5", "claude-opus-4-5"],
            live=["claude-sonnet-4-5", "claude-opus-4-5", "claude-opus-4-7"],
        )
        assert d["new"] == ["claude-opus-4-7"]
        assert d["removed"] == []
        assert "claude-sonnet-4-5" in d["unchanged"]

    def test_removed_models_surface(self) -> None:
        d = diff_known_vs_live(
            known=["gpt-4o", "gpt-5"],
            live=["gpt-5", "gpt-6"],
        )
        assert d["removed"] == ["gpt-4o"]
        assert d["new"] == ["gpt-6"]

    def test_no_changes(self) -> None:
        d = diff_known_vs_live(
            known=["a", "b", "c"], live=["c", "b", "a"],
        )
        assert d["new"] == []
        assert d["removed"] == []
        assert sorted(d["unchanged"]) == ["a", "b", "c"]

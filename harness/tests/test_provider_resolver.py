"""Tests for provider auto-resolver — picks tier-appropriate model per agent.

Pins:
  - codegen-strong agents NEVER receive avoided models (e.g. M2.7)
  - genesis_step_tiers takes precedence over category mapping
  - cred-driven preference: first model whose api_key_env is set wins
  - fallback to planning-medium when category isn't registered
  - coverage_report flags uncovered tiers
  - domain-local override merges per-top-key (not deep-merge)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from harness.core.provider_resolver import (
    coverage_report,
    load_tiers,
    resolve_provider_for_agent,
)


@pytest.fixture
def tiers() -> dict[str, Any]:
    return load_tiers()


# ────────────────────────────────────────────────────────────────
# load_tiers — registry shape


def test_framework_registry_has_required_tiers(tiers: dict[str, Any]) -> None:
    assert "tiers" in tiers
    for required_tier in (
        "codegen-strong",
        "planning-medium",
        "review-medium",
        "classification-light",
    ):
        assert required_tier in tiers["tiers"], f"missing tier: {required_tier}"


def test_codegen_strong_lists_m27_in_avoid(tiers: dict[str, Any]) -> None:
    """Regression — observed in this session: M2.7 fails CodeWriter
    `len(files) > 0` gates. Must NEVER be a codegen-strong recommendation."""
    avoid = set(tiers["tiers"]["codegen-strong"]["avoid_models"])
    assert "m2.7" in avoid


def test_category_to_tier_maps_codegen_to_strong(tiers: dict[str, Any]) -> None:
    cat_map = tiers["category_to_tier"]
    for category in ("codegen", "fast-codegen", "code", "writer", "migration"):
        assert cat_map.get(category) == "codegen-strong", (
            f"{category} must route to codegen-strong"
        )


def test_genesis_architect_pinned_to_codegen_strong(tiers: dict[str, Any]) -> None:
    step_map = tiers["genesis_step_tiers"]
    assert step_map["_genesis/AgentArchitectAgent/v2"] == "codegen-strong"
    assert step_map["_genesis/AgentBuilderAgent/v1"] == "codegen-strong"


# ────────────────────────────────────────────────────────────────
# resolve_provider_for_agent


def test_resolves_first_available_in_tier_order(tiers: dict[str, Any]) -> None:
    """Anthropic available → must pick claude (the codegen-strong leader)."""
    spec = resolve_provider_for_agent(
        category="codegen",
        tiers_doc=tiers,
        available_creds={"ANTHROPIC_API_KEY"},
    )
    assert spec is not None
    assert spec["provider"] == "anthropic"
    assert "claude" in spec["model"].lower()
    assert spec["api_key_env"] == "ANTHROPIC_API_KEY"
    assert spec["_resolved_tier"] == "codegen-strong"


def test_falls_through_when_first_choice_creds_missing(tiers: dict[str, Any]) -> None:
    """Only GLM available → falls to GLM (2nd in codegen-strong order)."""
    spec = resolve_provider_for_agent(
        category="codegen",
        tiers_doc=tiers,
        available_creds={"ZHIPU_API_KEY"},
    )
    assert spec is not None
    assert spec["model"] == "glm-5.1"
    assert spec["base_url"] == "https://open.bigmodel.cn/api/anthropic"


def test_codegen_never_routes_to_minimax(tiers: dict[str, Any]) -> None:
    """Even when ONLY MiniMax creds are present, codegen-strong must
    refuse. Returns None — caller should fall back to domain default
    or surface a coverage warning."""
    spec = resolve_provider_for_agent(
        category="codegen",
        tiers_doc=tiers,
        available_creds={"MINIMAX_API_KEY"},
    )
    assert spec is None


def test_classification_can_use_minimax(tiers: dict[str, Any]) -> None:
    """M2.7 IS in classification-light's recommended list (small outputs OK)."""
    spec = resolve_provider_for_agent(
        category="triage",
        tiers_doc=tiers,
        available_creds={"MINIMAX_API_KEY"},
    )
    assert spec is not None
    assert spec["model"] == "m2.7"
    assert spec["_resolved_tier"] == "classification-light"


def test_genesis_id_overrides_category(tiers: dict[str, Any]) -> None:
    """Architect is `category: reasoning` (would map to planning-medium) but
    its agent_id pin promotes it to codegen-strong."""
    spec = resolve_provider_for_agent(
        agent_id="_genesis/AgentArchitectAgent/v2",
        category="reasoning",
        tiers_doc=tiers,
        available_creds={"ANTHROPIC_API_KEY", "GOOGLE_API_KEY"},
    )
    assert spec is not None
    assert spec["_resolved_tier"] == "codegen-strong"
    assert "claude" in spec["model"].lower()


def test_unknown_category_falls_back_to_planning_medium(tiers: dict[str, Any]) -> None:
    spec = resolve_provider_for_agent(
        category="some-novel-category-not-registered",
        tiers_doc=tiers,
        available_creds={"GOOGLE_API_KEY"},
    )
    assert spec is not None
    assert spec["_resolved_tier"] == "planning-medium"


def test_returns_none_when_no_creds_at_all(tiers: dict[str, Any]) -> None:
    spec = resolve_provider_for_agent(
        category="codegen",
        tiers_doc=tiers,
        available_creds=set(),
    )
    assert spec is None


# ────────────────────────────────────────────────────────────────
# coverage_report


def test_coverage_report_lists_all_tiers(tiers: dict[str, Any]) -> None:
    rep = coverage_report(tiers_doc=tiers, available_creds={"ANTHROPIC_API_KEY"})
    assert "tiers" in rep
    for t in ("codegen-strong", "planning-medium", "review-medium", "classification-light"):
        assert t in rep["tiers"]


def test_coverage_report_flags_missing_envs(tiers: dict[str, Any]) -> None:
    rep = coverage_report(tiers_doc=tiers, available_creds=set())
    assert rep["missing_for_full"], "no creds → every tier has unfilled envs"
    # codegen-strong should still report which env vars would unlock it
    cg = rep["tiers"]["codegen-strong"]
    assert cg["covered_by"] is None
    assert "ANTHROPIC_API_KEY" in cg["missing_envs"]


def test_coverage_report_marks_covered_when_creds_present(
    tiers: dict[str, Any],
) -> None:
    rep = coverage_report(tiers_doc=tiers, available_creds={"ANTHROPIC_API_KEY"})
    assert rep["tiers"]["codegen-strong"]["covered_by"] is not None


# ────────────────────────────────────────────────────────────────
# Domain-local override merge


def test_domain_override_replaces_tier_recommended(tmp_path: Path) -> None:
    """A domain-local override must REPLACE the framework's recommended list
    for that tier, not splice. (Shallow merge per top-level key.)"""
    domain = tmp_path / "my-domain"
    (domain / "config").mkdir(parents=True)
    (domain / "config" / "model_tiers.yaml").write_text(yaml.safe_dump({
        "tiers": {
            "codegen-strong": {
                "avoid_models": [],
                "recommended": [
                    {"provider": "anthropic", "model": "custom-only",
                     "api_key_env": "MY_CUSTOM_KEY"},
                ],
            },
        },
    }))
    merged = load_tiers(domain_root=domain)
    cg_models = [
        s["model"] for s in merged["tiers"]["codegen-strong"]["recommended"]
    ]
    assert cg_models == ["custom-only"]
    # Other tiers untouched
    assert "tiers" in merged
    assert "planning-medium" in merged["tiers"]


def test_domain_override_can_add_to_category_map(tmp_path: Path) -> None:
    """category_to_tier merges per-key — both framework + override entries
    coexist (Python dict.update semantics inside _shallow_merge)."""
    domain = tmp_path / "my-domain"
    (domain / "config").mkdir(parents=True)
    (domain / "config" / "model_tiers.yaml").write_text(yaml.safe_dump({
        "category_to_tier": {
            "my-novel-category": "codegen-strong",
        },
    }))
    merged = load_tiers(domain_root=domain)
    assert merged["category_to_tier"]["my-novel-category"] == "codegen-strong"
    assert merged["category_to_tier"]["codegen"] == "codegen-strong"  # framework intact

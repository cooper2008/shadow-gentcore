"""Tests for B8 — Factory vs Builder role split."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

BUILDER_MANIFEST = (
    PROJECT_ROOT / "agents/_genesis/AgentBuilderAgent/v1/agent_manifest.yaml"
)
FACTORY_MANIFEST = (
    PROJECT_ROOT / "agents/_factory/AgentFactoryAgent/v1/agent_manifest.yaml"
)
ROLE_DOC = PROJECT_ROOT / "docs/FACTORY_VS_BUILDER.md"


@pytest.fixture(scope="module")
def builder() -> dict:
    return yaml.safe_load(BUILDER_MANIFEST.read_text())


@pytest.fixture(scope="module")
def factory() -> dict:
    return yaml.safe_load(FACTORY_MANIFEST.read_text())


class TestDescriptorsClarifyRoles:
    def test_builder_describes_full_domain_bootstrap(self, builder: dict) -> None:
        desc = builder["description"].lower()
        # Must mark itself as the full-domain bootstrap role
        assert "full domain" in desc or "full domain bootstrap" in desc
        # Must point to Factory for the single-agent escape hatch
        assert "factory" in desc

    def test_factory_describes_single_agent_synthesis(self, factory: dict) -> None:
        desc = factory["description"].lower()
        # Must mark itself as on-demand single-agent synthesis
        assert "single-agent" in desc or "one new agent" in desc or "one agent" in desc
        # Must point to Builder for the full-domain path
        assert "builder" in desc

    def test_factory_mentions_capabilityrecipe(self, factory: dict) -> None:
        """Factory's input contract is a CapabilityRecipe per audit §2 B8."""
        assert "CapabilityRecipe" in factory["description"]

    def test_both_agents_reference_role_doc(self, builder: dict, factory: dict) -> None:
        """Cross-referencing the docs keeps the role split discoverable
        even for teams scanning manifests in isolation."""
        assert "FACTORY_VS_BUILDER" in builder["description"]
        assert "FACTORY_VS_BUILDER" in factory["description"]


class TestIdentityUnchanged:
    """B8 is documentation-only — ids + versions + workflow wiring stable."""

    def test_builder_id_stable(self, builder: dict) -> None:
        assert builder["id"] == "_genesis/AgentBuilderAgent/v1"
        assert builder["version"] == "1.0.0"

    def test_factory_id_stable(self, factory: dict) -> None:
        assert factory["id"] == "_factory/AgentFactoryAgent/v1"
        assert factory["version"] == "1.0.0"

    def test_genesis_workflow_still_calls_builder_v1(self) -> None:
        wf = yaml.safe_load(
            (PROJECT_ROOT / "workflows/genesis/genesis_build.yaml").read_text()
        )
        agents = {s.get("agent") for s in wf.get("steps", [])}
        assert "_genesis/AgentBuilderAgent/v1" in agents

    def test_factory_workflow_still_calls_factory_v1(self) -> None:
        wf_path = PROJECT_ROOT / "workflows/factory/learn_and_create.yaml"
        if not wf_path.exists():
            pytest.skip("factory workflow not in this checkout")
        wf = yaml.safe_load(wf_path.read_text())
        agents = {s.get("agent") for s in wf.get("steps", [])}
        assert "_factory/AgentFactoryAgent/v1" in agents


class TestRoleDocPresent:
    def test_role_doc_exists(self) -> None:
        assert ROLE_DOC.exists()

    def test_role_doc_covers_both_agents(self) -> None:
        text = ROLE_DOC.read_text()
        assert "_genesis/AgentBuilderAgent/v1" in text
        assert "_factory/AgentFactoryAgent/v1" in text

    def test_role_doc_has_tldr_table(self) -> None:
        text = ROLE_DOC.read_text()
        # Must include the short-form scope table so readers get the split
        # in under 30 seconds
        assert "TL;DR" in text or "## TL;DR" in text
        assert "FULL domain" in text
        assert "ONE agent" in text

    def test_role_doc_explains_when_to_use_each(self) -> None:
        text = ROLE_DOC.read_text()
        assert "When to use Builder" in text
        assert "When to use Factory" in text

    def test_role_doc_flags_future_b6_convergence(self) -> None:
        """B6 (workflow convergence) is a separate audit item — B8 only
        clarifies the role split. The doc must flag that relationship so
        future readers don't try to ship B6 inside B8."""
        text = ROLE_DOC.read_text()
        assert "B6" in text

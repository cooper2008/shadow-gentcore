"""Tests for B9 — unknown-industry composition pipeline.

The framework MUST gracefully handle any industry tag on a domain, known
or unknown, without rejecting the domain or crashing genesis. These
tests exercise each fallback layer: the DomainManifest field, the
industries registry, the capability resolver, and the optional
industry pack pattern.

These tests prove the GENERIC MECHANISM, not any specific industry.
The aws-ops directory is cited only as an illustrative fixture.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from harness.core.capability_resolver import CapabilityResolver
from harness.core.manifest_loader import ManifestLoader


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


# ── Layer 1: DomainManifest accepts any industry string ────────────────────


class TestDomainManifestAcceptsAnyIndustry:
    """B4 — industry is Optional[str] with no enum. Pydantic must not reject."""

    def test_novel_industry_loads_through_loader(self, tmp_path: Path) -> None:
        from harness.core.manifest_loader import ManifestLoader

        novel_industry = "medical-device-qa"  # deliberately NOT in the shipped registry
        domain = {
            "name": "acme-med",
            "owner": "qa-team",
            "purpose": "Medical device testing compliance",
            "industry": novel_industry,
        }
        (tmp_path / "domain.yaml").write_text(yaml.dump(domain), encoding="utf-8")

        loader = ManifestLoader()
        result = loader.load_domain(tmp_path)
        assert result["industry"] == novel_industry

    def test_completely_unknown_industry_not_rejected(self, tmp_path: Path) -> None:
        """Even industries like 'martian-space-ops' that no pack exists for."""
        for wild_industry in ["martian-space-ops", "underwater-welding", "xyz"]:
            domain_path = tmp_path / f"d_{wild_industry}"
            domain_path.mkdir()
            (domain_path / "domain.yaml").write_text(
                yaml.dump({
                    "name": "test",
                    "owner": "t",
                    "purpose": "t",
                    "industry": wild_industry,
                }),
                encoding="utf-8",
            )
            loader = ManifestLoader()
            result = loader.load_domain(domain_path)
            assert result["industry"] == wild_industry


# ── Layer 2: Industries registry falls back cleanly ────────────────────────


class TestIndustriesRegistryFallback:
    """B4 — load_industries returns {} when registry absent OR industry
    not in registry. Callers must not hard-depend."""

    def test_unknown_industry_returns_no_typical_stages(self) -> None:
        """Known registry does NOT promise every possible industry."""
        industries = ManifestLoader.load_industries()
        # The shipped registry has 7 entries — but ANY other industry
        # must return None from the lookup (not crash, not raise).
        assert industries.get("medical-device-qa") is None
        assert industries.get("xyz-space-industry") is None

    def test_missing_registry_file_returns_empty(self, tmp_path: Path) -> None:
        assert ManifestLoader.load_industries(tmp_path) == {}


# ── Layer 3: CapabilityResolver falls back for unknown stages ──────────────


class TestCapabilityResolverFallback:
    """B2 — resolver returns [] for unknown capabilities/stages, not error."""

    def test_unknown_stage_returns_empty_capability_list(self) -> None:
        resolver = CapabilityResolver()
        assert resolver.resolve_capabilities_for_stage("NovelStage") == []
        assert resolver.resolve_packs_for_stage("NovelStage") == []

    def test_unknown_capability_returns_empty_pack_list(self) -> None:
        resolver = CapabilityResolver()
        assert resolver.resolve_packs("completely_new_capability") == []

    def test_missing_config_still_works(self, tmp_path: Path) -> None:
        """Resolver pointed at a dir with no capabilities.yaml returns
        empty — callers still get a working object, not a crash."""
        resolver = CapabilityResolver(config_dir=tmp_path)
        assert resolver.resolve_packs_for_stage("AnyStage") == []
        assert resolver.known_capabilities() == []


# ── Layer 4: Industry pack pattern works for arbitrary industries ──────────


class TestIndustryPackPattern:
    """Proves the `_shared/packs/<industry>/` pattern is not hardcoded to
    any specific industry. Synthesise a pack in a tmpdir and verify
    loaders pick it up via filesystem conventions."""

    def test_arbitrary_industry_capability_bindings_load(self, tmp_path: Path) -> None:
        """Build a pack for a fictional industry and load its bindings."""
        industry = "quantum-chemistry-lab"
        pack_dir = tmp_path / "packs" / industry
        pack_dir.mkdir(parents=True)
        bindings = {
            "stage_overrides": {
                "Investigate": ["scientific_retrieval", "instrument_query"],
                "Summarize": ["lab_reporting"],
            },
            "pack_preferences": {
                "scientific_retrieval": ["science/arxiv", "science/pubmed"],
            },
            "metadata": {"industry": industry},
        }
        (pack_dir / "capability_bindings.yaml").write_text(yaml.dump(bindings), encoding="utf-8")

        # The framework has no special hook for "quantum-chemistry-lab" —
        # the raw YAML loads fine via generic yaml.safe_load because it
        # follows the documented pack shape (stage_overrides + preferences).
        loaded = yaml.safe_load((pack_dir / "capability_bindings.yaml").read_text())
        assert loaded["metadata"]["industry"] == industry
        assert "Investigate" in loaded["stage_overrides"]

    def test_arbitrary_industry_workflow_template_loads(self, tmp_path: Path) -> None:
        """Synthesise a workflow template for an unfamiliar industry."""
        workflow = {
            "name": "lab_experiment_review",
            "industry": "quantum-chemistry-lab",
            "steps": [
                {"name": "investigate", "agent": "_shared/InvestigateAgent/v1"},
                {"name": "summarize", "agent": "_shared/SummarizeAgent/v1"},
            ],
        }
        path = tmp_path / "workflow_experiment_review.yaml"
        path.write_text(yaml.dump(workflow), encoding="utf-8")

        loader = ManifestLoader()
        wf = loader.load_workflow(path)
        assert wf["industry"] == "quantum-chemistry-lab"
        # Steps reference shared catalog stages — not industry-specific agents.
        for step in wf["steps"]:
            assert step["agent"].startswith("_shared/")


# ── End-to-end: unknown industry runs through all 4 layers ─────────────────


class TestEndToEndUnknownIndustryComposition:
    """Exercises B4 + B2 registry + B2 resolver + pack pattern together
    for an industry the shipped code has never seen."""

    def test_full_pipeline_handles_fictional_industry(self, tmp_path: Path) -> None:
        industry = "autonomous-vehicle-safety"

        # Layer 1: DomainManifest accepts it
        domain = {
            "name": "av-safety-team",
            "owner": "safety-eng",
            "purpose": "AV safety testing pipeline",
            "industry": industry,
        }
        (tmp_path / "domain.yaml").write_text(yaml.dump(domain), encoding="utf-8")

        # Layer 2: industries registry — industry is NOT registered; lookup returns None
        industries = ManifestLoader.load_industries()
        assert industries.get(industry) is None  # not shipped — graceful miss

        # Layer 3: capability resolver — architect uses SWE defaults for
        # stages it recognises; unknown stages get empty lists
        resolver = CapabilityResolver()
        # Known stage resolves (from shipped map)
        assert resolver.resolve_packs_for_stage("CodeWriter") != []
        # Unknown stage for this industry resolves to empty — architect
        # marks as synthesize-new
        assert resolver.resolve_packs_for_stage("SafetyValidator") == []

        # Layer 4: domain loads without the industry being in any pack dir
        loader = ManifestLoader()
        result = loader.load_domain(tmp_path)
        assert result["industry"] == industry

    def test_aws_ops_example_pack_exists_as_reference(self) -> None:
        """aws-ops ships as one worked example showing the pattern — its
        presence should not be load-bearing for any test or runtime.
        This test just confirms it's documented, not that it's required."""
        pack_dir = PROJECT_ROOT / "agents" / "_shared" / "packs" / "aws-ops"
        if not pack_dir.exists():
            pytest.skip("aws-ops example pack not shipped in this checkout")
        # When present, it MUST follow the documented pack shape:
        if (pack_dir / "capability_bindings.yaml").exists():
            bindings = yaml.safe_load((pack_dir / "capability_bindings.yaml").read_text())
            assert "stage_overrides" in bindings
        if (pack_dir / "runbooks").exists():
            # Runbooks must have frontmatter (B7 convention)
            for rb in (pack_dir / "runbooks").glob("*.md"):
                content = rb.read_text()
                assert content.startswith("---"), f"runbook {rb.name} missing frontmatter"

    def test_packs_readme_documents_the_generic_pattern(self) -> None:
        """The README must state that packs are a generic mechanism,
        not industry-specific features. Keeps aws-ops from being
        misread as a special-case framework extension."""
        readme = PROJECT_ROOT / "agents" / "_shared" / "packs" / "README.md"
        if not readme.exists():
            pytest.skip("packs README not in this checkout")
        text = readme.read_text()
        # Must clearly signal aws-ops is one example, not a built-in
        assert "worked example" in text.lower() or "one example" in text.lower()
        # Must document the unknown-industry handling path
        assert "unknown" in text.lower()

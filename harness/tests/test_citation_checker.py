"""Tier-citation enforcement tests.

Covers the citation foundation committed as the 'real hallucination fix'
per cross-model review. Runtime enforcement (wiring into AgentRunner +
gate expressions) follows in a later commit; this tier just validates
extraction, scoring, and rule semantics.
"""

from __future__ import annotations

import pytest

from harness.core.citation_checker import (
    Citation,
    CitationReport,
    extract_citations,
    score_citations,
    validate_citations,
)


class TestExtractCitations:
    def test_top_level_citations_extracted(self):
        output = {
            "citations": [
                {"claim": "Use APIRouter", "tier": "T2", "source": "fastapi_routers"},
            ],
            "status": "success",
        }
        result = extract_citations(output)
        assert len(result) == 1
        assert result[0].claim == "Use APIRouter"
        assert result[0].tier == "T2"
        assert result[0].source == "fastapi_routers"

    def test_meta_citations_nested_fallback(self):
        output = {"meta": {"citations": [
            {"claim": "C", "tier": "T1", "source": "standards"},
        ]}}
        result = extract_citations(output)
        assert len(result) == 1

    def test_missing_citations_returns_empty(self):
        assert extract_citations({}) == []
        assert extract_citations({"other": "data"}) == []

    def test_non_dict_output_returns_empty(self):
        assert extract_citations("string") == []
        assert extract_citations(None) == []

    def test_empty_claim_dropped(self):
        output = {"citations": [
            {"claim": "", "tier": "T2", "source": "x"},
            {"claim": "  ", "tier": "T2", "source": "y"},
            {"claim": "kept", "tier": "T2", "source": "z"},
        ]}
        result = extract_citations(output)
        assert len(result) == 1
        assert result[0].claim == "kept"

    def test_tier_aliases_normalized(self):
        output = {"citations": [
            {"claim": "a", "tier": "context_retrieve", "source": "x"},
            {"claim": "b", "tier": "standards.md", "source": "y"},
            {"claim": "c", "tier": "t3", "source": "z"},
        ]}
        result = extract_citations(output)
        assert [c.tier for c in result] == ["T2", "T1", "T3"]


class TestValidateCitations:
    def test_empty_claim_is_error(self):
        cits = [Citation(claim="", tier="T2", source="x")]
        findings = validate_citations(cits)
        assert any(f.severity == "error" for f in findings)

    def test_unknown_tier_warns(self):
        cits = [Citation(claim="c", tier="T99", source="x")]
        findings = validate_citations(cits)
        assert any("unknown tier" in f.message.lower() for f in findings)

    def test_factual_tier_without_source_warns(self):
        cits = [Citation(claim="c", tier="T2", source="")]
        findings = validate_citations(cits)
        assert any("empty source" in f.message.lower() for f in findings)

    def test_self_tier_without_source_allowed(self):
        """Agent's own reasoning doesn't need a source."""
        cits = [Citation(claim="I think X", tier="self", source="")]
        findings = validate_citations(cits)
        # No error/warn for source emptiness on self
        assert not any("empty source" in f.message.lower() for f in findings)

    def test_require_tiers_enforces_presence(self):
        cits = [Citation(claim="c", tier="T1", source="standards")]
        findings = validate_citations(cits, require_tiers=["T2"])
        assert any("Required tier T2" in f.message for f in findings)

    def test_require_tiers_passes_when_present(self):
        cits = [
            Citation(claim="a", tier="T1", source="standards"),
            Citation(claim="b", tier="T2", source="chunk"),
        ]
        findings = validate_citations(cits, require_tiers=["T2"])
        assert not any("Required tier" in f.message for f in findings)


class TestScoreCitations:
    def test_full_coverage_scores_1(self):
        output = {"citations": [
            {"claim": "a", "tier": "T1", "source": "standards"},
            {"claim": "b", "tier": "T2", "source": "chunk_x"},
        ]}
        report = score_citations(output)
        assert report.score == 1.0
        assert report.cited_claims == 2
        assert report.total_claims == 2

    def test_missing_sources_drop_score(self):
        output = {"citations": [
            {"claim": "a", "tier": "T1", "source": "standards"},  # ok (T1 doesn't need explicit source)
            {"claim": "b", "tier": "T2", "source": ""},            # factual but no source
        ]}
        report = score_citations(output)
        # T1 is counted as cited even w/o source; T2 w/o source excluded.
        assert report.cited_claims == 1
        assert report.total_claims == 2
        assert 0.0 < report.score < 1.0

    def test_min_citations_enforced(self):
        output = {"citations": [
            {"claim": "a", "tier": "T1", "source": "standards"},
        ]}
        report = score_citations(output, min_citations=3)
        assert report.score == 0.0
        assert any(f.severity == "error" and "require_min" in f.message
                   for f in report.findings)

    def test_min_citations_neutral_when_zero(self):
        """When min_citations=0, absence of citations returns score 1.0 (neutral)."""
        report = score_citations({"other": "data"}, min_citations=0)
        assert report.score == 1.0

    def test_require_tiers_composes_with_score(self):
        output = {"citations": [
            {"claim": "a", "tier": "T1", "source": "standards"},
        ]}
        report = score_citations(output, require_tiers=["T2"])
        assert any(f.severity == "error" and "Required tier T2" in f.message
                   for f in report.findings)
        assert not report.passed

    def test_passed_reflects_errors_only(self):
        """warn findings don't break passed; error findings do."""
        output = {"citations": [
            {"claim": "a", "tier": "T99", "source": "x"},  # unknown tier → warn
        ]}
        report = score_citations(output)
        assert report.passed is True  # only warns, not errors

    def test_none_tier_counted_as_uncited(self):
        output = {"citations": [
            {"claim": "a", "tier": "none", "source": ""},
            {"claim": "b", "tier": "T2", "source": "chunk"},
        ]}
        report = score_citations(output)
        assert report.cited_claims == 1
        assert report.total_claims == 2

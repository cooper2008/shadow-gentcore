"""Tests for ContextEngine."""

from __future__ import annotations

import pytest

from harness.core.context_engine import ContextEngine


class TestContextEngine:
    def test_add_and_build(self) -> None:
        engine = ContextEngine()
        engine.add_item("repo_map", "src/ has code", priority=1)
        engine.add_item("readme", "Project readme", priority=0)
        result = engine.build()
        assert len(result) == 2
        assert result[0]["source"] == "repo_map"  # higher priority first

    def test_priority_ordering(self) -> None:
        engine = ContextEngine()
        engine.add_item("low", "low content", priority=0)
        engine.add_item("high", "high content", priority=10)
        engine.add_item("mid", "mid content", priority=5)
        result = engine.build()
        assert [r["source"] for r in result] == ["high", "mid", "low"]

    def test_token_budget_compaction(self) -> None:
        engine = ContextEngine(max_tokens=100)
        engine.add_item("big", "x" * 800, priority=10)  # ~200 tokens
        engine.add_item("small", "y" * 40, priority=5)  # ~10 tokens
        result = engine.build()
        # Should include truncated big + maybe small
        assert len(result) >= 1
        assert result[0]["source"] == "big"

    def test_no_budget_returns_all(self) -> None:
        engine = ContextEngine()
        for i in range(10):
            engine.add_item(f"src_{i}", f"content {i}")
        result = engine.build()
        assert len(result) == 10

    def test_reset(self) -> None:
        engine = ContextEngine()
        engine.add_item("a", "content a")
        engine.add_item("b", "content b")
        assert engine.item_count == 2
        engine.reset()
        assert engine.item_count == 0
        assert engine.build() == []

    def test_total_token_estimate(self) -> None:
        engine = ContextEngine()
        engine.add_item("a", "x" * 100, token_estimate=25)
        engine.add_item("b", "y" * 200, token_estimate=50)
        assert engine.total_token_estimate == 75

    def test_auto_token_estimate(self) -> None:
        engine = ContextEngine()
        engine.add_item("a", "x" * 100)  # auto ~25 tokens
        assert engine.total_token_estimate == 25

    def test_empty_build(self) -> None:
        engine = ContextEngine()
        assert engine.build() == []

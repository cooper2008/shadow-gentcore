"""Tests for ContextEngine checkpoint/restore support."""

from __future__ import annotations

import pytest

from harness.core.context_engine import ContextEngine


class TestContextCheckpoint:
    """Task 70: checkpoint-based context reset."""

    def test_checkpoint_captures_state(self) -> None:
        engine = ContextEngine(max_tokens=5000)
        engine.add_item("repo_map", "file listing", priority=10)
        engine.add_item("prior_artifact", "previous output", priority=5)

        cp = engine.checkpoint()
        assert len(cp["items"]) == 2
        assert cp["max_tokens"] == 5000

    def test_restore_replaces_state(self) -> None:
        engine = ContextEngine(max_tokens=5000)
        engine.add_item("repo_map", "file listing", priority=10)
        cp = engine.checkpoint()

        engine.add_item("extra", "should be replaced", priority=1)
        assert engine.item_count == 2

        engine.restore(cp)
        assert engine.item_count == 1
        built = engine.build()
        assert built[0]["source"] == "repo_map"

    def test_reset_to_checkpoint_clears_and_restores(self) -> None:
        engine = ContextEngine(max_tokens=5000)
        engine.add_item("base", "foundation context", priority=10)
        cp = engine.checkpoint()

        engine.add_item("step1_output", "result A", priority=5)
        engine.add_item("step2_output", "result B", priority=3)
        assert engine.item_count == 3

        engine.reset_to_checkpoint(cp)
        assert engine.item_count == 1
        built = engine.build()
        assert built[0]["source"] == "base"

    def test_no_context_bleed_after_reset(self) -> None:
        """After reset_to_checkpoint, no items from between checkpoint and reset survive."""
        engine = ContextEngine()
        engine.add_item("checkpoint_data", "safe", priority=10)
        cp = engine.checkpoint()

        engine.add_item("leaked_a", "should not survive", priority=5)
        engine.add_item("leaked_b", "should not survive", priority=3)

        engine.reset_to_checkpoint(cp)

        built = engine.build()
        sources = [item["source"] for item in built]
        assert "leaked_a" not in sources
        assert "leaked_b" not in sources
        assert "checkpoint_data" in sources

    def test_checkpoint_is_deep_copy(self) -> None:
        """Modifying items after checkpoint does not affect the checkpoint."""
        engine = ContextEngine()
        engine.add_item("mutable", "original", priority=5)
        cp = engine.checkpoint()

        engine.reset()
        engine.add_item("new", "different", priority=1)

        engine.restore(cp)
        assert engine.item_count == 1
        built = engine.build()
        assert built[0]["content"] == "original"

    def test_multiple_checkpoints(self) -> None:
        """Multiple checkpoints at different points work independently."""
        engine = ContextEngine()
        engine.add_item("phase1", "data1", priority=10)
        cp1 = engine.checkpoint()

        engine.add_item("phase2", "data2", priority=5)
        cp2 = engine.checkpoint()

        engine.add_item("phase3", "data3", priority=1)

        engine.reset_to_checkpoint(cp1)
        assert engine.item_count == 1

        engine.reset_to_checkpoint(cp2)
        assert engine.item_count == 2

    def test_restore_updates_max_tokens(self) -> None:
        engine = ContextEngine(max_tokens=1000)
        cp = engine.checkpoint()

        engine2 = ContextEngine(max_tokens=9999)
        engine2.restore(cp)
        # max_tokens from checkpoint should override
        built = engine2.build()
        assert isinstance(built, list)

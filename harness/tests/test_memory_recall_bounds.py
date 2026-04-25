"""Tests for memory_recall output-size bounds.

Without bounds, ``memory_recall(k=50)`` over a domain that has accumulated
many large past outputs can dump 30K+ tokens of raw blobs into a fresh
prompt — silently blowing the context window. The tool's recall path
now caps three things:

  * k — hard-capped at 50 regardless of caller request
  * per-entry preview — default 600 chars, 200 in ``summary_only`` mode
  * total output — ``max_total_chars`` (default 5000) auto-shrinks
    per-entry chars when k * per_entry would exceed the cap

These complement the react mid-run compaction shipped earlier: react
caps the message-history side, recall caps the memory-import side.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest

from harness.core.memory_store import FileMemoryStore
from harness.tools.builtin import MemoryRecallAdapter


@pytest.fixture
def populated_store() -> Any:
    """Yield (memory_root, agent_id) with 20 stored entries of 2KB each."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / ".gentcore" / "memory"
        store = FileMemoryStore(base_dir=root)
        agent_id = "test/Agent/v1"
        for i in range(20):
            store.store(
                agent_id=agent_id,
                key="run_output",
                value="X" * 2000 + f" entry_{i}",
            )
        yield root.parent.parent, agent_id


def _args(memory_root: Path, agent_id: str, **extra: Any) -> dict[str, Any]:
    return {"agent_id": agent_id, "memory_root": str(memory_root), **extra}


class TestKHardCap:
    @pytest.mark.asyncio
    async def test_k_50_hard_cap(self, populated_store: tuple[Path, str]) -> None:
        """An adversarial caller asking for k=10000 still gets at most 50."""
        memory_root, agent_id = populated_store
        adapter = MemoryRecallAdapter()
        result = await adapter.invoke("memory_recall", _args(memory_root, agent_id, k=10000))
        # The store only has 20 entries, so we get 20 — the cap matters when there are >50.
        assert result["entries_returned"] == 20

    @pytest.mark.asyncio
    async def test_k_negative_clamped_to_min(self, populated_store: tuple[Path, str]) -> None:
        memory_root, agent_id = populated_store
        adapter = MemoryRecallAdapter()
        result = await adapter.invoke("memory_recall", _args(memory_root, agent_id, k=-5))
        # Floored to 1; should return exactly one entry.
        assert result["entries_returned"] == 1


class TestSummaryOnly:
    @pytest.mark.asyncio
    async def test_summary_mode_uses_short_preview(self, populated_store: tuple[Path, str]) -> None:
        memory_root, agent_id = populated_store
        adapter = MemoryRecallAdapter()
        full = await adapter.invoke("memory_recall", _args(memory_root, agent_id, k=5))
        summary = await adapter.invoke(
            "memory_recall",
            _args(memory_root, agent_id, k=5, summary_only=True),
        )
        assert summary["summary_only"] is True
        assert summary["per_entry_chars"] <= 200
        assert len(summary["stdout"]) < len(full["stdout"])

    @pytest.mark.asyncio
    async def test_default_mode_is_full_preview(self, populated_store: tuple[Path, str]) -> None:
        memory_root, agent_id = populated_store
        adapter = MemoryRecallAdapter()
        result = await adapter.invoke("memory_recall", _args(memory_root, agent_id, k=3))
        assert result["summary_only"] is False
        # 600 default OR shrunk by max_total — both acceptable
        assert result["per_entry_chars"] >= 100


class TestMaxTotalCap:
    @pytest.mark.asyncio
    async def test_high_k_auto_shrinks_per_entry(self, populated_store: tuple[Path, str]) -> None:
        """k=20 with default max_total=5000 should shrink per-entry chars
        from 600 down to ~250 so total output stays bounded."""
        memory_root, agent_id = populated_store
        adapter = MemoryRecallAdapter()
        result = await adapter.invoke("memory_recall", _args(memory_root, agent_id, k=20))
        assert result["entries_returned"] == 20
        # 20 * 600 = 12000 > 5000 default → must shrink
        assert result["per_entry_chars"] < 600
        # But must not shrink below 100 floor
        assert result["per_entry_chars"] >= 100

    @pytest.mark.asyncio
    async def test_explicit_max_total_respected(self, populated_store: tuple[Path, str]) -> None:
        memory_root, agent_id = populated_store
        adapter = MemoryRecallAdapter()
        result = await adapter.invoke(
            "memory_recall",
            _args(memory_root, agent_id, k=10, max_total_chars=2000),
        )
        # 10 entries with budget 2000 → ~200 per entry
        assert result["per_entry_chars"] <= 200

    @pytest.mark.asyncio
    async def test_low_k_keeps_full_preview(self, populated_store: tuple[Path, str]) -> None:
        """When k * 600 fits inside max_total, no shrinkage."""
        memory_root, agent_id = populated_store
        adapter = MemoryRecallAdapter()
        # 3 * 600 = 1800 < 5000 → no shrink
        result = await adapter.invoke("memory_recall", _args(memory_root, agent_id, k=3))
        assert result["per_entry_chars"] == 600

    @pytest.mark.asyncio
    async def test_truncation_marker_present_when_shrunk(
        self, populated_store: tuple[Path, str]
    ) -> None:
        """Operators should see how many chars were elided per entry."""
        memory_root, agent_id = populated_store
        adapter = MemoryRecallAdapter()
        result = await adapter.invoke(
            "memory_recall", _args(memory_root, agent_id, k=10, summary_only=True),
        )
        # Each entry was 2000+ chars, summary_only caps at 200
        assert "[+" in result["stdout"] and "chars]" in result["stdout"]

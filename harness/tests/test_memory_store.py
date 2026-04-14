"""Tests for harness.core.memory_store — FileMemoryStore and InMemoryMemoryStore."""
from __future__ import annotations

import time

import pytest

from harness.core.memory_store import FileMemoryStore, InMemoryMemoryStore, MemoryStore


# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------

def test_in_memory_store_satisfies_protocol():
    assert isinstance(InMemoryMemoryStore(), MemoryStore)


def test_file_store_satisfies_protocol(tmp_path):
    assert isinstance(FileMemoryStore(base_dir=tmp_path), MemoryStore)


# ---------------------------------------------------------------------------
# InMemoryMemoryStore
# ---------------------------------------------------------------------------

class TestInMemoryMemoryStore:
    def test_store_and_recall_all(self):
        store = InMemoryMemoryStore()
        store.store("agent-a", "run_output", "hello world")
        entries = store.recall("agent-a")
        assert len(entries) == 1
        assert entries[0]["value"] == "hello world"
        assert entries[0]["key"] == "run_output"

    def test_recall_empty_store_returns_empty_list(self):
        store = InMemoryMemoryStore()
        assert store.recall("agent-a") == []

    def test_recall_with_key_filter(self):
        store = InMemoryMemoryStore()
        store.store("agent-a", "run_output", "output value")
        store.store("agent-a", "other_key", "other value")
        entries = store.recall("agent-a", key="run_output")
        assert len(entries) == 1
        assert entries[0]["value"] == "output value"

    def test_recall_key_filter_no_match(self):
        store = InMemoryMemoryStore()
        store.store("agent-a", "run_output", "output value")
        entries = store.recall("agent-a", key="nonexistent")
        assert entries == []

    def test_recall_k_returns_last_k_entries(self):
        store = InMemoryMemoryStore()
        for i in range(5):
            store.store("agent-a", "run_output", f"value-{i}")
        entries = store.recall("agent-a", k=2)
        assert len(entries) == 2
        assert entries[0]["value"] == "value-3"
        assert entries[1]["value"] == "value-4"

    def test_clear_removes_all_memories(self):
        store = InMemoryMemoryStore()
        store.store("agent-a", "run_output", "something")
        store.clear("agent-a")
        assert store.recall("agent-a") == []

    def test_memory_is_agent_scoped(self):
        store = InMemoryMemoryStore()
        store.store("agent-a", "run_output", "a's memory")
        store.store("agent-b", "run_output", "b's memory")
        a_entries = store.recall("agent-a")
        b_entries = store.recall("agent-b")
        assert len(a_entries) == 1
        assert a_entries[0]["value"] == "a's memory"
        assert len(b_entries) == 1
        assert b_entries[0]["value"] == "b's memory"

    def test_clear_only_affects_target_agent(self):
        store = InMemoryMemoryStore()
        store.store("agent-a", "run_output", "a's memory")
        store.store("agent-b", "run_output", "b's memory")
        store.clear("agent-a")
        assert store.recall("agent-a") == []
        assert len(store.recall("agent-b")) == 1

    def test_metadata_stored_and_recalled(self):
        store = InMemoryMemoryStore()
        store.store("agent-a", "run_output", "val", metadata={"task_id": "t1"})
        entry = store.recall("agent-a")[0]
        assert entry["metadata"]["task_id"] == "t1"

    def test_timestamp_is_set(self):
        store = InMemoryMemoryStore()
        before = time.time()
        store.store("agent-a", "run_output", "val")
        after = time.time()
        entry = store.recall("agent-a")[0]
        assert before <= entry["timestamp"] <= after


# ---------------------------------------------------------------------------
# FileMemoryStore
# ---------------------------------------------------------------------------

class TestFileMemoryStore:
    def test_store_and_recall_all(self, tmp_path):
        store = FileMemoryStore(base_dir=tmp_path)
        store.store("agent-a", "run_output", "file hello")
        entries = store.recall("agent-a")
        assert len(entries) == 1
        assert entries[0]["value"] == "file hello"

    def test_recall_empty_store_returns_empty_list(self, tmp_path):
        store = FileMemoryStore(base_dir=tmp_path)
        assert store.recall("agent-a") == []

    def test_recall_with_key_filter(self, tmp_path):
        store = FileMemoryStore(base_dir=tmp_path)
        store.store("agent-a", "run_output", "output value")
        store.store("agent-a", "other_key", "other value")
        entries = store.recall("agent-a", key="run_output")
        assert len(entries) == 1
        assert entries[0]["value"] == "output value"

    def test_recall_k_returns_last_k_entries(self, tmp_path):
        store = FileMemoryStore(base_dir=tmp_path)
        for i in range(5):
            store.store("agent-a", "run_output", f"value-{i}")
        entries = store.recall("agent-a", k=2)
        assert len(entries) == 2
        assert entries[0]["value"] == "value-3"
        assert entries[1]["value"] == "value-4"

    def test_clear_removes_all_memories(self, tmp_path):
        store = FileMemoryStore(base_dir=tmp_path)
        store.store("agent-a", "run_output", "something")
        store.clear("agent-a")
        assert store.recall("agent-a") == []

    def test_clear_nonexistent_agent_is_noop(self, tmp_path):
        store = FileMemoryStore(base_dir=tmp_path)
        store.clear("does-not-exist")  # must not raise

    def test_memory_is_agent_scoped(self, tmp_path):
        store = FileMemoryStore(base_dir=tmp_path)
        store.store("agent-a", "run_output", "a's memory")
        store.store("agent-b", "run_output", "b's memory")
        a_entries = store.recall("agent-a")
        b_entries = store.recall("agent-b")
        assert a_entries[0]["value"] == "a's memory"
        assert b_entries[0]["value"] == "b's memory"

    def test_slash_in_agent_id_is_sanitised(self, tmp_path):
        store = FileMemoryStore(base_dir=tmp_path)
        store.store("team/agent-a", "run_output", "scoped value")
        entries = store.recall("team/agent-a")
        assert len(entries) == 1
        # Directory must exist on disk and not contain a literal slash
        safe_dir = tmp_path / "team_agent-a"
        assert safe_dir.is_dir()

    def test_metadata_stored_and_recalled(self, tmp_path):
        store = FileMemoryStore(base_dir=tmp_path)
        store.store("agent-a", "run_output", "val", metadata={"task_id": "t42"})
        entry = store.recall("agent-a")[0]
        assert entry["metadata"]["task_id"] == "t42"

    def test_persists_across_instances(self, tmp_path):
        store1 = FileMemoryStore(base_dir=tmp_path)
        store1.store("agent-a", "run_output", "persistent value")

        store2 = FileMemoryStore(base_dir=tmp_path)
        entries = store2.recall("agent-a")
        assert len(entries) == 1
        assert entries[0]["value"] == "persistent value"


# ---------------------------------------------------------------------------
# FileMemoryStore — max_entries compaction
# ---------------------------------------------------------------------------

class TestFileMemoryStoreMaxEntries:
    def test_max_entries_keeps_only_last_n(self, tmp_path):
        """Storing 10 entries with max_entries=5 retains only the 5 most recent."""
        store = FileMemoryStore(base_dir=tmp_path, max_entries=5)
        for i in range(10):
            store.store("agent-a", "key", f"value-{i}")
        entries = store.recall("agent-a", k=100)
        assert len(entries) == 5
        values = [e["value"] for e in entries]
        assert values == [f"value-{i}" for i in range(5, 10)]

    def test_max_entries_does_not_evict_when_under_limit(self, tmp_path):
        """Storing fewer entries than max_entries must keep them all."""
        store = FileMemoryStore(base_dir=tmp_path, max_entries=10)
        for i in range(4):
            store.store("agent-a", "key", f"value-{i}")
        entries = store.recall("agent-a", k=100)
        assert len(entries) == 4

    def test_max_entries_scoped_per_agent(self, tmp_path):
        """Eviction for agent-a must not affect agent-b."""
        store = FileMemoryStore(base_dir=tmp_path, max_entries=3)
        for i in range(6):
            store.store("agent-a", "key", f"a-value-{i}")
        store.store("agent-b", "key", "b-only")
        a_entries = store.recall("agent-a", k=100)
        b_entries = store.recall("agent-b", k=100)
        assert len(a_entries) == 3
        assert len(b_entries) == 1

    def test_compaction_preserves_recent_entries(self, tmp_path):
        """After compaction the retained entries must be the chronologically newest ones."""
        store = FileMemoryStore(base_dir=tmp_path, max_entries=3)
        for i in range(5):
            store.store("agent-a", "key", f"value-{i}")
        entries = store.recall("agent-a", k=100)
        values = [e["value"] for e in entries]
        assert values == ["value-2", "value-3", "value-4"]

    def test_compact_rewrites_file_not_appends(self, tmp_path):
        """The JSONL file line count must equal max_entries after compaction."""
        store = FileMemoryStore(base_dir=tmp_path, max_entries=3)
        for i in range(7):
            store.store("agent-a", "key", f"value-{i}")
        memories_file = tmp_path / "agent-a" / "memories.jsonl"
        lines = [l for l in memories_file.read_text().splitlines() if l.strip()]
        assert len(lines) == 3


# ---------------------------------------------------------------------------
# FileMemoryStore — max_age_seconds TTL eviction
# ---------------------------------------------------------------------------

class TestFileMemoryStoreMaxAge:
    def test_max_age_evicts_old_entries(self, tmp_path):
        """Entries older than max_age_seconds are removed during compaction."""
        store = FileMemoryStore(base_dir=tmp_path, max_age_seconds=0.05)
        store.store("agent-a", "key", "old-value")
        time.sleep(0.1)  # let the entry age past the TTL
        store.store("agent-a", "key", "new-value")  # triggers compaction
        entries = store.recall("agent-a", k=100)
        values = [e["value"] for e in entries]
        assert "old-value" not in values
        assert "new-value" in values

    def test_max_age_keeps_recent_entries(self, tmp_path):
        """Entries within the TTL window must survive compaction."""
        store = FileMemoryStore(base_dir=tmp_path, max_age_seconds=60)
        store.store("agent-a", "key", "fresh-value")
        store.store("agent-a", "key", "another-fresh")
        entries = store.recall("agent-a", k=100)
        assert len(entries) == 2

    def test_max_age_none_disables_age_eviction(self, tmp_path):
        """When max_age_seconds is None no age-based eviction occurs."""
        store = FileMemoryStore(base_dir=tmp_path, max_entries=100, max_age_seconds=None)
        for i in range(5):
            store.store("agent-a", "key", f"value-{i}")
        entries = store.recall("agent-a", k=100)
        assert len(entries) == 5

    def test_max_age_combined_with_max_entries(self, tmp_path):
        """Age eviction runs before count eviction; both limits are respected."""
        store = FileMemoryStore(base_dir=tmp_path, max_entries=2, max_age_seconds=0.05)
        store.store("agent-a", "key", "stale-1")
        store.store("agent-a", "key", "stale-2")
        time.sleep(0.1)
        store.store("agent-a", "key", "fresh-1")
        store.store("agent-a", "key", "fresh-2")
        store.store("agent-a", "key", "fresh-3")  # triggers compaction with max_entries=2
        entries = store.recall("agent-a", k=100)
        values = [e["value"] for e in entries]
        assert "stale-1" not in values
        assert "stale-2" not in values
        assert len(entries) == 2
        assert values == ["fresh-2", "fresh-3"]


# ---------------------------------------------------------------------------
# InMemoryMemoryStore — max_entries
# ---------------------------------------------------------------------------

class TestInMemoryMemoryStoreMaxEntries:
    def test_max_entries_keeps_only_last_n(self):
        """Storing 10 entries with max_entries=5 retains only the 5 most recent."""
        store = InMemoryMemoryStore(max_entries=5)
        for i in range(10):
            store.store("agent-a", "key", f"value-{i}")
        entries = store.recall("agent-a", k=100)
        assert len(entries) == 5
        values = [e["value"] for e in entries]
        assert values == [f"value-{i}" for i in range(5, 10)]

    def test_max_entries_default_is_100(self):
        """Default max_entries=100 must allow storing 100 entries without eviction."""
        store = InMemoryMemoryStore()
        for i in range(100):
            store.store("agent-a", "key", f"value-{i}")
        entries = store.recall("agent-a", k=200)
        assert len(entries) == 100

    def test_max_entries_evicts_on_101st_store(self):
        """The 101st store with default max_entries=100 drops the oldest entry."""
        store = InMemoryMemoryStore()
        for i in range(101):
            store.store("agent-a", "key", f"value-{i}")
        entries = store.recall("agent-a", k=200)
        assert len(entries) == 100
        assert entries[0]["value"] == "value-1"  # value-0 was evicted

    def test_max_entries_scoped_per_agent(self):
        """Eviction for agent-a must not affect agent-b."""
        store = InMemoryMemoryStore(max_entries=3)
        for i in range(6):
            store.store("agent-a", "key", f"a-value-{i}")
        store.store("agent-b", "key", "b-only")
        assert len(store.recall("agent-a", k=100)) == 3
        assert len(store.recall("agent-b", k=100)) == 1

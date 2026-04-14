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

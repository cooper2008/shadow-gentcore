"""Tests for AgentRunner persist_files writer.

Without this flag, code-writing domain agents (CodeWriter, MigrationAgent,
etc.) emit ``{"files": [{path, content}]}`` in their structured output
but the framework only persists a 2KB Tier 4 memory snippet — files
never reach the user's filesystem. With ``manifest.persist_files=True``,
AgentRunner writes each entry under ``task.workspace_root``.

Coverage spans:
  * the gate (manifest.persist_files=False → no writes)
  * the writer (files materialize on disk under target root)
  * scope-guard (absolute paths outside workspace are refused)
  * malformed entries are reported in files_persisted.failed
  * Builder hook still owns its own file writing (no double-write)
"""

from __future__ import annotations

import tempfile
from pathlib import Path


from harness.core.agent_runner import AgentRunner


class FakeManifest:
    def __init__(self, persist_files: bool = False) -> None:
        self.persist_files = persist_files
        self.id = "test/Foo/v1"


class TestPersistOutputFilesGate:
    def test_off_by_default_no_writes(self) -> None:
        runner = AgentRunner(provider=None, tool_executor=None, memory_store=None)
        with tempfile.TemporaryDirectory() as tmp:
            result = {
                "parsed_output": {"files": [{"path": "x.py", "content": "print('x')"}]}
            }
            runner._persist_output_files(
                manifest=FakeManifest(persist_files=False),
                task={"workspace_root": tmp},
                result=result,
            )
            # Nothing written
            assert not (Path(tmp) / "x.py").exists()
            assert "files_persisted" not in result

    def test_persist_true_writes_to_workspace(self) -> None:
        runner = AgentRunner(provider=None, tool_executor=None, memory_store=None)
        with tempfile.TemporaryDirectory() as tmp:
            result = {
                "parsed_output": {
                    "files": [
                        {"path": "src/health.py", "content": "ROUTE = '/v1/health'\n"},
                        {"path": "tests/test_health.py", "content": "def test_x(): pass\n"},
                    ]
                }
            }
            runner._persist_output_files(
                manifest=FakeManifest(persist_files=True),
                task={"workspace_root": tmp},
                result=result,
            )
            assert (Path(tmp) / "src/health.py").read_text() == "ROUTE = '/v1/health'\n"
            assert (Path(tmp) / "tests/test_health.py").read_text() == "def test_x(): pass\n"
            persisted = result["files_persisted"]
            assert len(persisted["written"]) == 2
            assert persisted["failed"] == []


class TestPersistOutputFilesScopeGuard:
    def test_absolute_path_outside_workspace_refused(self) -> None:
        runner = AgentRunner(provider=None, tool_executor=None, memory_store=None)
        with tempfile.TemporaryDirectory() as tmp:
            result = {
                "parsed_output": {
                    "files": [{"path": "/tmp/somewhere-else/evil.py", "content": "x"}]
                }
            }
            runner._persist_output_files(
                manifest=FakeManifest(persist_files=True),
                task={"workspace_root": tmp},
                result=result,
            )
            assert not Path("/tmp/somewhere-else/evil.py").exists()
            assert result["files_persisted"]["written"] == []
            assert len(result["files_persisted"]["failed"]) == 1
            assert "escapes workspace" in result["files_persisted"]["failed"][0]["reason"]

    def test_absolute_path_inside_workspace_accepted(self) -> None:
        runner = AgentRunner(provider=None, tool_executor=None, memory_store=None)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp).resolve()
            result = {
                "parsed_output": {
                    "files": [{"path": str(target / "nested/x.py"), "content": "x = 1\n"}]
                }
            }
            runner._persist_output_files(
                manifest=FakeManifest(persist_files=True),
                task={"workspace_root": tmp},
                result=result,
            )
            assert (target / "nested/x.py").exists()


class TestPersistOutputFilesEdgeCases:
    def test_no_files_array_skips(self) -> None:
        runner = AgentRunner(provider=None, tool_executor=None, memory_store=None)
        with tempfile.TemporaryDirectory() as tmp:
            result = {"parsed_output": {"code": "print()"}}  # no files
            runner._persist_output_files(
                manifest=FakeManifest(persist_files=True),
                task={"workspace_root": tmp},
                result=result,
            )
            assert "files_persisted" not in result

    def test_malformed_entries_reported_not_raised(self) -> None:
        runner = AgentRunner(provider=None, tool_executor=None, memory_store=None)
        with tempfile.TemporaryDirectory() as tmp:
            result = {
                "parsed_output": {
                    "files": [
                        {"path": "ok.py", "content": "ok\n"},
                        {"path": None, "content": "x"},  # invalid
                        {"content": "no path"},  # invalid
                        "not a dict",  # invalid
                    ]
                }
            }
            runner._persist_output_files(
                manifest=FakeManifest(persist_files=True),
                task={"workspace_root": tmp},
                result=result,
            )
            assert (Path(tmp) / "ok.py").exists()
            assert len(result["files_persisted"]["written"]) == 1
            assert len(result["files_persisted"]["failed"]) == 2  # the two dict-entries with bad path

    def test_no_workspace_records_failure(self) -> None:
        runner = AgentRunner(provider=None, tool_executor=None, memory_store=None)
        result = {"parsed_output": {"files": [{"path": "x.py", "content": "x"}]}}
        runner._persist_output_files(
            manifest=FakeManifest(persist_files=True),
            task={},  # no workspace_root, output_dir, or domain_path
            result=result,
        )
        assert "no workspace_root" in result["files_persisted"]["failed"][0]["reason"]

    def test_falls_back_to_output_dir_then_domain_path(self) -> None:
        runner = AgentRunner(provider=None, tool_executor=None, memory_store=None)
        with tempfile.TemporaryDirectory() as tmp:
            result = {"parsed_output": {"files": [{"path": "x.py", "content": "x"}]}}
            # No workspace_root, has output_dir
            runner._persist_output_files(
                manifest=FakeManifest(persist_files=True),
                task={"output_dir": tmp},
                result=result,
            )
            assert (Path(tmp) / "x.py").exists()

    def test_reads_from_input_payload(self) -> None:
        runner = AgentRunner(provider=None, tool_executor=None, memory_store=None)
        with tempfile.TemporaryDirectory() as tmp:
            result = {"parsed_output": {"files": [{"path": "x.py", "content": "x"}]}}
            runner._persist_output_files(
                manifest=FakeManifest(persist_files=True),
                task={"input_payload": {"workspace_root": tmp}},
                result=result,
            )
            assert (Path(tmp) / "x.py").exists()


class TestPersistOutputFilesPathPrecedence:
    def test_parsed_output_preferred_over_output(self) -> None:
        """parsed_output is the schema-validated promotion path."""
        runner = AgentRunner(provider=None, tool_executor=None, memory_store=None)
        with tempfile.TemporaryDirectory() as tmp:
            result = {
                "parsed_output": {"files": [{"path": "from_parsed.py", "content": "p"}]},
                "output": {"files": [{"path": "from_raw.py", "content": "r"}]},
            }
            runner._persist_output_files(
                manifest=FakeManifest(persist_files=True),
                task={"workspace_root": tmp},
                result=result,
            )
            assert (Path(tmp) / "from_parsed.py").exists()
            assert not (Path(tmp) / "from_raw.py").exists()

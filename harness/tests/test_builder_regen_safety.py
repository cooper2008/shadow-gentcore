"""Tests for AgentBuilder regen-safety guard.

Before this guard, re-running ``./ai genesis build --domain X`` silently
overwrote any hand-edits a user had made to generated files (agents,
workflows, prompts). The guard:

  1. Records a SHA-256 of every file Builder writes into
     ``{domain}/.gentcore/genesis-manifest.json``.
  2. On the next genesis run, before overwriting an existing file,
     hashes the current on-disk content. If it differs from the
     recorded hash → user has hand-edited → SKIP by default.
  3. Override channels: ``GENTCORE_FORCE_OVERWRITE=1`` env var,
     ``task.force_overwrite=True``, or ``task.input_payload.force_overwrite=True``.

The skipped files are reported in ``files_skipped_user_modified`` and
counted toward ``completion_pct`` (the user owns them now — that's
not a failure).
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import pytest

# Import the post_execute hook by file location since it's not a normal package.
import importlib.util


def _load_hooks_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "builder_hooks",
        "/Users/yiminguo/shadow-gentcore/agents/_genesis/AgentBuilderAgent/v1/hooks.py",
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


HOOKS = _load_hooks_module()


def _fresh_task(out_dir: Path, **extra: Any) -> dict[str, Any]:
    return {
        "task_id": "test",
        "agent_id": "test/Builder/v1",
        "output_dir": str(out_dir),
        **extra,
    }


def _result_with_files(files: list[dict[str, str]]) -> dict[str, Any]:
    return {"output": {"files": files}, "status": "success"}


@pytest.fixture
def domain_root() -> Any:
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


class TestSha256Helper:
    def test_deterministic(self) -> None:
        h1 = HOOKS._sha256("hello")
        h2 = HOOKS._sha256("hello")
        assert h1 == h2 and len(h1) == 64

    def test_differs_on_content_change(self) -> None:
        assert HOOKS._sha256("hello") != HOOKS._sha256("hello!")


class TestForceOverwriteFlag:
    def test_default_false(self) -> None:
        assert HOOKS._force_overwrite_enabled({}) is False

    def test_env_var_enables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GENTCORE_FORCE_OVERWRITE", "1")
        assert HOOKS._force_overwrite_enabled({}) is True

    def test_env_var_accepts_yes_true_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for v in ("yes", "true", "on", "YES", "True"):
            monkeypatch.setenv("GENTCORE_FORCE_OVERWRITE", v)
            assert HOOKS._force_overwrite_enabled({}) is True, f"failed for {v!r}"

    def test_env_var_rejects_other(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for v in ("0", "false", "no", "off", ""):
            monkeypatch.setenv("GENTCORE_FORCE_OVERWRITE", v)
            assert HOOKS._force_overwrite_enabled({}) is False

    def test_top_level_task_flag(self) -> None:
        assert HOOKS._force_overwrite_enabled({"force_overwrite": True}) is True
        assert HOOKS._force_overwrite_enabled({"force_overwrite": False}) is False

    def test_input_payload_flag(self) -> None:
        assert HOOKS._force_overwrite_enabled({"input_payload": {"force_overwrite": True}}) is True


class TestGenesisManifestIO:
    def test_load_returns_empty_when_missing(self, domain_root: Path) -> None:
        path = HOOKS._genesis_manifest_path(domain_root)
        assert HOOKS._load_genesis_manifest(path) == {}

    def test_round_trip(self, domain_root: Path) -> None:
        path = HOOKS._genesis_manifest_path(domain_root)
        data = {"agents/X/v1/system_prompt.md": {"hash": "abc", "generated_at": 1234.5}}
        HOOKS._save_genesis_manifest(path, data)
        assert path.exists()
        assert HOOKS._load_genesis_manifest(path) == data

    def test_corrupt_manifest_returns_empty(self, domain_root: Path) -> None:
        path = HOOKS._genesis_manifest_path(domain_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not json {{{", encoding="utf-8")
        assert HOOKS._load_genesis_manifest(path) == {}


class TestPostExecuteFirstRun:
    def test_first_run_writes_files_and_creates_manifest(self, domain_root: Path) -> None:
        files = [
            {"path": "agents/Foo/v1/system_prompt.md", "content": "You are Foo."},
            {"path": "workflows/main.yaml", "content": "name: main\nsteps: []\n"},
        ]
        result = HOOKS.post_execute(
            manifest=None,
            task=_fresh_task(domain_root),
            result=_result_with_files(files),
        )

        assert result["status"] == "success"
        assert len(result["output"]["files_created"]) == 2
        assert result["output"]["files_skipped_user_modified"] == []
        assert (domain_root / "agents/Foo/v1/system_prompt.md").read_text() == "You are Foo."

        manifest_path = domain_root / ".gentcore/genesis-manifest.json"
        assert manifest_path.exists()
        recorded = json.loads(manifest_path.read_text())
        assert "agents/Foo/v1/system_prompt.md" in recorded
        assert recorded["agents/Foo/v1/system_prompt.md"]["hash"] == HOOKS._sha256("You are Foo.")


class TestPostExecuteRegen:
    def test_unchanged_file_overwritten_silently(self, domain_root: Path) -> None:
        """Regen of a file the user hasn't touched is a normal overwrite."""
        files = [{"path": "agents/Foo/v1/system_prompt.md", "content": "v1 prompt"}]
        HOOKS.post_execute(None, _fresh_task(domain_root), _result_with_files(files))

        # Second run with new content; user hasn't touched it.
        files_v2 = [{"path": "agents/Foo/v1/system_prompt.md", "content": "v2 prompt"}]
        result = HOOKS.post_execute(None, _fresh_task(domain_root), _result_with_files(files_v2))

        assert result["status"] == "success"
        assert len(result["output"]["files_created"]) == 1
        assert result["output"]["files_skipped_user_modified"] == []
        assert (domain_root / "agents/Foo/v1/system_prompt.md").read_text() == "v2 prompt"

    def test_user_edited_file_skipped(self, domain_root: Path) -> None:
        """The core safety guarantee: hand-edited files are NOT overwritten."""
        files = [{"path": "agents/Foo/v1/system_prompt.md", "content": "generated v1"}]
        HOOKS.post_execute(None, _fresh_task(domain_root), _result_with_files(files))

        # User customizes the file
        target = domain_root / "agents/Foo/v1/system_prompt.md"
        target.write_text("MY HAND EDIT — do not lose", encoding="utf-8")

        # Re-genesis with new content
        files_v2 = [{"path": "agents/Foo/v1/system_prompt.md", "content": "generated v2"}]
        result = HOOKS.post_execute(None, _fresh_task(domain_root), _result_with_files(files_v2))

        # The user's edit survives
        assert target.read_text() == "MY HAND EDIT — do not lose"
        # And we report what was skipped + why
        skipped = result["output"]["files_skipped_user_modified"]
        assert len(skipped) == 1
        assert skipped[0]["path"] == "agents/Foo/v1/system_prompt.md"
        assert "modified since last genesis" in skipped[0]["reason"]

    def test_force_overwrite_env_overrides_skip(
        self, domain_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        files = [{"path": "agents/Foo/v1/system_prompt.md", "content": "generated v1"}]
        HOOKS.post_execute(None, _fresh_task(domain_root), _result_with_files(files))

        target = domain_root / "agents/Foo/v1/system_prompt.md"
        target.write_text("MY EDIT", encoding="utf-8")

        monkeypatch.setenv("GENTCORE_FORCE_OVERWRITE", "1")
        files_v2 = [{"path": "agents/Foo/v1/system_prompt.md", "content": "generated v2"}]
        result = HOOKS.post_execute(None, _fresh_task(domain_root), _result_with_files(files_v2))

        assert target.read_text() == "generated v2"
        assert result["output"]["files_skipped_user_modified"] == []

    def test_force_overwrite_task_flag_overrides_skip(self, domain_root: Path) -> None:
        files = [{"path": "agents/Foo/v1/system_prompt.md", "content": "v1"}]
        HOOKS.post_execute(None, _fresh_task(domain_root), _result_with_files(files))

        target = domain_root / "agents/Foo/v1/system_prompt.md"
        target.write_text("EDIT", encoding="utf-8")

        files_v2 = [{"path": "agents/Foo/v1/system_prompt.md", "content": "v2"}]
        task = _fresh_task(domain_root, force_overwrite=True)
        result = HOOKS.post_execute(None, task, _result_with_files(files_v2))

        assert target.read_text() == "v2"
        assert result["output"]["files_skipped_user_modified"] == []

    def test_skipped_files_count_toward_completion(self, domain_root: Path) -> None:
        """Regen-skips are not failures — completion_pct should reflect that."""
        files = [
            {"path": "agents/A/v1/x.md", "content": "v1A"},
            {"path": "agents/B/v1/x.md", "content": "v1B"},
        ]
        HOOKS.post_execute(None, _fresh_task(domain_root), _result_with_files(files))

        # User edits A, leaves B alone
        (domain_root / "agents/A/v1/x.md").write_text("USER", encoding="utf-8")

        files_v2 = [
            {"path": "agents/A/v1/x.md", "content": "v2A"},
            {"path": "agents/B/v1/x.md", "content": "v2B"},
        ]
        result = HOOKS.post_execute(None, _fresh_task(domain_root), _result_with_files(files_v2))

        bq = result["output"]["build_quality"]
        assert bq["files_planned"] == 2
        assert bq["files_written"] == 1
        assert bq["files_skipped_user_modified"] == 1
        assert bq["completion_pct"] == 100  # 1 written + 1 skipped = full coverage

    def test_skipped_files_do_not_update_manifest_hash(self, domain_root: Path) -> None:
        """If we skipped a file, the manifest hash for it must NOT be updated to
        the new generated content — otherwise the next regen run would think
        the user's edit IS the generated version."""
        files = [{"path": "agents/Foo/v1/x.md", "content": "v1"}]
        HOOKS.post_execute(None, _fresh_task(domain_root), _result_with_files(files))
        original_v1_hash = HOOKS._sha256("v1")

        target = domain_root / "agents/Foo/v1/x.md"
        target.write_text("USER", encoding="utf-8")

        files_v2 = [{"path": "agents/Foo/v1/x.md", "content": "v2"}]
        HOOKS.post_execute(None, _fresh_task(domain_root), _result_with_files(files_v2))

        manifest_path = domain_root / ".gentcore/genesis-manifest.json"
        recorded = json.loads(manifest_path.read_text())
        # hash should still be v1's hash (the last value we actually wrote),
        # not v2's — so a third regen still detects the user edit.
        assert recorded["agents/Foo/v1/x.md"]["hash"] == original_v1_hash

        # Verify: a third regen still skips
        files_v3 = [{"path": "agents/Foo/v1/x.md", "content": "v3"}]
        result3 = HOOKS.post_execute(None, _fresh_task(domain_root), _result_with_files(files_v3))
        assert len(result3["output"]["files_skipped_user_modified"]) == 1
        assert target.read_text() == "USER"

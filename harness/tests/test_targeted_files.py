"""Tests for G6 — targeted-files allowlist for Builder retries."""

from __future__ import annotations

from typing import Any

import pytest

from harness.core.tool_executor import ToolExecutor


async def _noop_adapter(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "wrote": arguments.get("path", "")}


@pytest.fixture()
def executor() -> ToolExecutor:
    te = ToolExecutor()
    te.register_adapter("file_write", _noop_adapter)
    te.register_adapter("file_edit", _noop_adapter)
    te.register_adapter("file_read", _noop_adapter)
    return te


class TestAllowlistUnsetBackwardCompat:
    """Default: no enforcement, all file_write calls go through."""

    @pytest.mark.asyncio
    async def test_file_write_unrestricted_when_allowlist_unset(self, executor: ToolExecutor) -> None:
        result = await executor.execute({
            "id": "t1", "name": "file_write",
            "arguments": {"path": "anything.txt", "content": "x"},
        })
        assert result["success"] is True
        assert "blocked_by_targeted_files" not in result

    @pytest.mark.asyncio
    async def test_explicit_none_is_no_enforcement(self, executor: ToolExecutor) -> None:
        executor.set_targeted_files(None)
        result = await executor.execute({
            "id": "t1", "name": "file_write",
            "arguments": {"path": "x.txt", "content": "x"},
        })
        assert result["success"] is True


class TestAllowlistEnforcement:
    """Allowlist set → only paths in it may be written."""

    @pytest.mark.asyncio
    async def test_allowed_path_passes(self, executor: ToolExecutor) -> None:
        executor.set_targeted_files(["agents/X/v1/system_prompt.md"])
        result = await executor.execute({
            "id": "t1", "name": "file_write",
            "arguments": {"path": "agents/X/v1/system_prompt.md", "content": "prompt"},
        })
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_blocked_path_denied(self, executor: ToolExecutor) -> None:
        executor.set_targeted_files(["agents/X/v1/system_prompt.md"])
        result = await executor.execute({
            "id": "t1", "name": "file_write",
            "arguments": {"path": "agents/Y/v1/agent_manifest.yaml", "content": "x"},
        })
        assert result["success"] is False
        assert result.get("blocked_by_targeted_files") is True
        assert "allowlist" in result["output"].lower()

    @pytest.mark.asyncio
    async def test_file_edit_also_enforced(self, executor: ToolExecutor) -> None:
        executor.set_targeted_files(["context/standards.md"])
        result = await executor.execute({
            "id": "t2", "name": "file_edit",
            "arguments": {"path": "agents/X/v1/agent_manifest.yaml", "changes": []},
        })
        assert result.get("blocked_by_targeted_files") is True

    @pytest.mark.asyncio
    async def test_file_read_not_restricted(self, executor: ToolExecutor) -> None:
        """Read operations are OUT of scope — retries need to see prior files."""
        executor.set_targeted_files(["only/writeable.md"])
        result = await executor.execute({
            "id": "t3", "name": "file_read",
            "arguments": {"path": "some/other/file.md"},
        })
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_dotslash_prefix_normalised(self, executor: ToolExecutor) -> None:
        """Agents sometimes emit ./foo — match against foo in the allowlist."""
        executor.set_targeted_files(["foo/bar.md"])
        result = await executor.execute({
            "id": "t4", "name": "file_write",
            "arguments": {"path": "./foo/bar.md", "content": "x"},
        })
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_allowlist_in_allowlist_side_normalised_too(self, executor: ToolExecutor) -> None:
        """Allowlist entries with ./ prefix also normalise."""
        executor.set_targeted_files(["./foo/bar.md"])
        result = await executor.execute({
            "id": "t5", "name": "file_write",
            "arguments": {"path": "foo/bar.md", "content": "x"},
        })
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_empty_allowlist_blocks_everything(self, executor: ToolExecutor) -> None:
        """Empty list means [] → falsy → set_targeted_files stores None → no enforcement.

        This matches the convention in set_targeted_files docstring. If callers
        want to block all writes, they should remove the file_write adapter, not
        pass an empty allowlist.
        """
        executor.set_targeted_files([])
        result = await executor.execute({
            "id": "t6", "name": "file_write",
            "arguments": {"path": "anything.txt", "content": "x"},
        })
        # Empty list → allowlist unset → no enforcement → write succeeds
        assert result["success"] is True


class TestAgentRunnerWiring:
    """AgentRunner reads task.input_payload.targeted_files and wires it + clears it."""

    @pytest.mark.asyncio
    async def test_runner_sets_and_clears_allowlist(self) -> None:
        from harness.core.agent_runner import AgentRunner

        class _FakeProvider:
            async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
                return {"content": '{"ok": true}', "tool_calls": [], "tokens_used": 1}

        recorded: list[tuple[str, Any]] = []

        class _SpyExecutor(ToolExecutor):
            def set_targeted_files(self, paths: list[str] | None) -> None:
                recorded.append(("set", paths))
                super().set_targeted_files(paths)

        te = _SpyExecutor()
        runner = AgentRunner(provider=_FakeProvider(), tool_executor=te)

        # Task carries a targeted_files allowlist (simulating a gate-triggered retry)
        task = {
            "input_payload": {
                "targeted_files": ["agents/X/v1/system_prompt.md"],
                "instruction": "Fix this file.",
            },
        }

        manifest = {
            "id": "_genesis/AgentBuilderAgent/v1",
            "domain": "_genesis",
            "category": "fast-codegen",
            "execution_mode": {"primary": "chain_of_thought"},
            "tools": [],
            "permissions": {},
            "system_prompt_ref": "system_prompt.md",
        }

        await runner.run(
            manifest=manifest,
            task=task,
            system_prompt_content="Rebuild the flagged files.",
        )

        # Must set at start and clear at finally
        assert ("set", ["agents/X/v1/system_prompt.md"]) in recorded
        assert ("set", None) in recorded
        # Order check: set happens before clear
        set_idx = next(i for i, (a, p) in enumerate(recorded) if a == "set" and p is not None)
        clear_idx = next(i for i, (a, p) in enumerate(recorded) if a == "set" and p is None)
        assert set_idx < clear_idx

    @pytest.mark.asyncio
    async def test_runner_no_targeted_files_no_wiring(self) -> None:
        """Parity: when task has no targeted_files, set_targeted_files is NOT called."""
        from harness.core.agent_runner import AgentRunner

        class _FakeProvider:
            async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
                return {"content": '{"ok": true}', "tool_calls": [], "tokens_used": 1}

        recorded: list[Any] = []

        class _SpyExecutor(ToolExecutor):
            def set_targeted_files(self, paths: list[str] | None) -> None:
                recorded.append(paths)
                super().set_targeted_files(paths)

        te = _SpyExecutor()
        runner = AgentRunner(provider=_FakeProvider(), tool_executor=te)

        task = {"input_payload": {"instruction": "Do the thing."}}
        manifest = {
            "id": "_genesis/AgentBuilderAgent/v1",
            "domain": "_genesis",
            "category": "fast-codegen",
            "execution_mode": {"primary": "chain_of_thought"},
            "tools": [],
            "permissions": {},
            "system_prompt_ref": "system_prompt.md",
        }

        await runner.run(
            manifest=manifest,
            task=task,
            system_prompt_content="Do the thing.",
        )
        assert recorded == []  # set_targeted_files was never invoked

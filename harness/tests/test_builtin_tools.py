"""Tests for built-in tool adapters."""

from __future__ import annotations

import shlex

import pytest

from harness.tools.builtin import BUILTIN_ADAPTERS, FileWriteAdapter, _q, register_builtins
from harness.core.tool_executor import ToolExecutor


class TestBuiltinAdapters:
    @pytest.mark.asyncio
    async def test_file_read_builds_correct_command(self) -> None:
        adapter = BUILTIN_ADAPTERS["file_read"]
        result = await adapter.invoke("file_read", {"path": "/etc/hostname"})
        # Just checks that the command ran (file may or may not exist)
        assert "exit_code" in result
        assert "stdout" in result

    @pytest.mark.asyncio
    async def test_file_write_and_read(self, tmp_path) -> None:  # type: ignore[misc]
        write_adapter = BUILTIN_ADAPTERS["file_write"]
        target = str(tmp_path / "test.txt")
        result = await write_adapter.invoke("file_write", {"path": target, "content": "hello world"})
        assert result["success"] is True

        read_adapter = BUILTIN_ADAPTERS["file_read"]
        read_result = await read_adapter.invoke("file_read", {"path": target})
        assert "hello world" in read_result["stdout"]

    @pytest.mark.asyncio
    async def test_shell_exec(self) -> None:
        adapter = BUILTIN_ADAPTERS["shell_exec"]
        result = await adapter.invoke("shell_exec", {"command": "echo hello"})
        assert result["success"] is True
        assert "hello" in result["stdout"]

    @pytest.mark.asyncio
    async def test_search_code(self) -> None:
        adapter = BUILTIN_ADAPTERS["search_code"]
        result = await adapter.invoke("search_code", {"pattern": "def test_", "path": "harness/tests"})
        assert "exit_code" in result  # Just runs without exception

    @pytest.mark.asyncio
    async def test_list_dir(self) -> None:
        adapter = BUILTIN_ADAPTERS["list_dir"]
        result = await adapter.invoke("list_dir", {"path": "."})
        assert result["exit_code"] == 0

    @pytest.mark.asyncio
    async def test_file_write_missing_path(self) -> None:
        adapter = FileWriteAdapter()
        result = await adapter.invoke("file_write", {})
        assert result["success"] is False
        assert "path" in result["stderr"]

    def test_register_builtins(self) -> None:
        executor = ToolExecutor()
        register_builtins(executor)
        # Spot-check a few
        assert "file_read" in executor._adapters
        assert "shell_exec" in executor._adapters
        assert "npm_run_test" in executor._adapters
        assert "pytest_asyncio" in executor._adapters


class TestShellInjectionPrevention:
    """Verify that user-supplied arguments cannot inject shell commands."""

    INJECTION_PAYLOADS = [
        "$(id)",
        "`id`",
        "'; id; echo '",
        '"; id; echo "',
        "/tmp/x; rm -rf /",
        "path && malicious_cmd",
        "path | cat /etc/passwd",
        "path\nmalicious_cmd",
    ]

    @pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
    def test_q_neutralizes_injection(self, payload: str) -> None:
        """_q() must produce a string that shlex.split() treats as a single token."""
        quoted = _q(payload)
        tokens = shlex.split(f"cat {quoted}")
        # Must be exactly 2 tokens: ["cat", <the_payload_as_one_arg>]
        assert len(tokens) == 2, f"_q({payload!r}) split into {len(tokens)} tokens: {tokens}"
        assert tokens[1] == payload, f"_q() altered the value: expected {payload!r}, got {tokens[1]!r}"

    @pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
    @pytest.mark.asyncio
    async def test_file_read_does_not_execute_injection(self, payload: str) -> None:
        """file_read with an injected path must not execute the injected command."""
        adapter = BUILTIN_ADAPTERS["file_read"]
        result = await adapter.invoke("file_read", {"path": payload})
        # The injection should not succeed — stdout must NOT contain uid= output
        stdout = result.get("stdout", "")
        assert "uid=" not in stdout, f"Injection payload {payload!r} leaked uid= in stdout"

    @pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
    @pytest.mark.asyncio
    async def test_list_dir_does_not_execute_injection(self, payload: str) -> None:
        """list_dir with an injected path must not execute the injected command."""
        adapter = BUILTIN_ADAPTERS["list_dir"]
        result = await adapter.invoke("list_dir", {"path": payload})
        stdout = result.get("stdout", "")
        assert "uid=" not in stdout, f"Injection payload {payload!r} leaked uid= in stdout"

    @pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
    @pytest.mark.asyncio
    async def test_search_code_does_not_execute_injection(self, payload: str) -> None:
        """search_code with an injected pattern must not execute the injected command."""
        adapter = BUILTIN_ADAPTERS["search_code"]
        result = await adapter.invoke("search_code", {"pattern": "def test", "path": payload})
        stdout = result.get("stdout", "")
        assert "uid=" not in stdout, f"Injection payload {payload!r} leaked uid= in stdout"

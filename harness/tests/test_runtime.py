"""Tests for BaseRuntime and LocalRuntime."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from harness.core.runtime import BaseRuntime, LocalRuntime


class TestBaseRuntime:
    def test_cannot_instantiate_abstract(self) -> None:
        with pytest.raises(TypeError):
            BaseRuntime()


class TestLocalRuntime:
    def test_resolve_workspace_creates_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            rt = LocalRuntime(workspace_root=tmpdir)
            ws = rt.resolve_workspace("backend")
            assert ws.exists()
            assert ws == Path(tmpdir) / "backend"

    def test_resolve_credentials_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-123")
        rt = LocalRuntime()
        creds = rt.resolve_credentials("anthropic")
        assert creds["api_key"] == "test-key-123"
        assert creds["provider"] == "anthropic"

    def test_resolve_credentials_missing(self) -> None:
        rt = LocalRuntime()
        creds = rt.resolve_credentials("nonexistent_provider")
        assert creds["api_key"] == ""

    def test_resolve_permissions_defaults_to_ask(self) -> None:
        rt = LocalRuntime()
        assert rt.resolve_permissions("file_edit", "backend/Agent/v1") == "ask"

    def test_output_mode_default(self) -> None:
        rt = LocalRuntime()
        assert rt.get_output_mode() == "console"

    def test_output_mode_custom(self) -> None:
        rt = LocalRuntime(output_mode="json")
        assert rt.get_output_mode() == "json"

    def test_storage_backend(self) -> None:
        mock_storage = {"type": "mock"}
        rt = LocalRuntime(storage_backend=mock_storage)
        assert rt.get_storage_backend() == mock_storage

    def test_workspace_root_property(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            rt = LocalRuntime(workspace_root=tmpdir)
            assert rt.workspace_root == Path(tmpdir)

"""Unit tests for source adapter framework."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from harness.core.source_adapters import (
    LocalFSAdapter,
    SourceAdapter,
    SourceSpec,
    register_adapter,
    resolve_source,
)
from harness.core.source_adapters.github import GitHubAdapter, _parse_github_uri
from harness.core.source_adapters.registry import (
    _parse_scheme,
    _resolve_credentials,
    list_schemes,
)


class TestUriParsing:
    def test_scheme_detected_for_known_schemes(self):
        assert _parse_scheme("github://org/repo") == "github"
        assert _parse_scheme("file:///abs/path") == "file"

    def test_bare_paths_default_to_file_scheme(self):
        assert _parse_scheme("/absolute/path") == "file"
        assert _parse_scheme("~/relative") == "file"
        assert _parse_scheme("./local") == "file"

    def test_builtin_adapters_registered(self):
        schemes = list_schemes()
        assert "file" in schemes
        assert "github" in schemes


class TestGitHubUriParser:
    def test_plain_org_repo(self):
        assert _parse_github_uri("github://acme/backend") == {
            "org": "acme", "repo": "backend", "ref": None, "subpath": None,
        }

    def test_with_ref(self):
        assert _parse_github_uri("github://acme/backend@main") == {
            "org": "acme", "repo": "backend", "ref": "main", "subpath": None,
        }

    def test_with_sha_ref(self):
        parsed = _parse_github_uri("github://acme/backend@abc123def")
        assert parsed["ref"] == "abc123def"

    def test_with_subpath(self):
        parsed = _parse_github_uri("github://acme/backend?path=src/app")
        assert parsed["subpath"] == "src/app"

    def test_combined_ref_and_subpath(self):
        parsed = _parse_github_uri("github://acme/backend@v1.2?path=src")
        assert parsed["ref"] == "v1.2"
        assert parsed["subpath"] == "src"

    def test_malformed_missing_repo(self):
        with pytest.raises(ValueError):
            _parse_github_uri("github://acme")

    def test_malformed_wrong_scheme(self):
        with pytest.raises(ValueError):
            _parse_github_uri("https://github.com/acme/repo")


class TestLocalFsAdapter:
    @pytest.mark.asyncio
    async def test_existing_path_returns_resolved(self, tmp_path):
        adapter = LocalFSAdapter()
        spec = SourceSpec(uri=str(tmp_path))
        result = await adapter.materialize(spec, {}, tmp_path / "cache")
        assert result == tmp_path.resolve()

    @pytest.mark.asyncio
    async def test_file_uri_prefix_stripped(self, tmp_path):
        adapter = LocalFSAdapter()
        spec = SourceSpec(uri=f"file://{tmp_path}")
        result = await adapter.materialize(spec, {}, tmp_path / "cache")
        assert result == tmp_path.resolve()

    @pytest.mark.asyncio
    async def test_missing_path_raises(self, tmp_path):
        adapter = LocalFSAdapter()
        spec = SourceSpec(uri="/definitely-does-not-exist-xyz")
        with pytest.raises(FileNotFoundError):
            await adapter.materialize(spec, {}, tmp_path)


class TestResolveSourceDispatch:
    @pytest.mark.asyncio
    async def test_local_string_dispatches_to_local_fs(self, tmp_path):
        (tmp_path / "x.txt").write_text("hi")
        result = await resolve_source(str(tmp_path))
        assert result.exists()

    @pytest.mark.asyncio
    async def test_dict_spec_dispatches(self, tmp_path):
        result = await resolve_source({"uri": str(tmp_path)})
        assert result.exists()

    @pytest.mark.asyncio
    async def test_sourcespec_dispatches(self, tmp_path):
        result = await resolve_source(SourceSpec(uri=str(tmp_path)))
        assert result.exists()

    @pytest.mark.asyncio
    async def test_unknown_scheme_raises(self):
        with pytest.raises(ValueError, match="No source adapter"):
            await resolve_source("myproto://foo/bar")


class TestCredentialResolution:
    def test_explicit_override_respected(self, monkeypatch):
        monkeypatch.setenv("MY_CUSTOM_TOKEN", "secret-value")
        creds = _resolve_credentials(["GITHUB_TOKEN"], explicit="MY_CUSTOM_TOKEN")
        assert creds == {"GITHUB_TOKEN": "secret-value"}

    def test_missing_required_raises(self, monkeypatch):
        monkeypatch.delenv("MADEUP_TOKEN_42", raising=False)
        with pytest.raises(RuntimeError, match="Missing required credentials"):
            _resolve_credentials(["MADEUP_TOKEN_42"])

    def test_empty_required_returns_empty(self):
        assert _resolve_credentials([]) == {}


class TestRegisterCustomAdapter:
    def test_can_register_custom_scheme(self):
        class MyAdapter(SourceAdapter):
            scheme = "custom"
            required_credentials: list[str] = []

            async def materialize(self, spec, creds, cache_dir):
                return cache_dir

        register_adapter(MyAdapter)
        assert "custom" in list_schemes()

    def test_missing_scheme_attribute_raises(self):
        class Bad:
            scheme = ""

        with pytest.raises(ValueError, match="missing a `scheme`"):
            register_adapter(Bad)  # type: ignore[arg-type]


class TestGitHubAdapterMocked:
    @pytest.mark.asyncio
    async def test_invalid_ref_raises_filenotfound(self, tmp_path, monkeypatch):
        """404 from GitHub → FileNotFoundError with clear message."""
        import httpx

        class Fake404Response:
            status_code = 404

            def raise_for_status(self):
                raise httpx.HTTPStatusError("404", request=None, response=self)  # type: ignore[arg-type]

        class FakeClient:
            def __init__(self, *a, **kw): ...
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, *a, **kw): return Fake404Response()

        monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
        adapter = GitHubAdapter()
        spec = SourceSpec(uri="github://nope/nothing@main")
        with pytest.raises(FileNotFoundError, match="not found"):
            await adapter.materialize(spec, {}, tmp_path)

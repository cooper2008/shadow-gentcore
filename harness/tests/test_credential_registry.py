"""Tests for harness.core.credential_registry + credential_backends.

Covers tool-pack ingestion (new credentials: + legacy credential_source
alias), derived credentials from an agent manifest, validate() happy +
missing paths, MissingCredentialError messaging, and each backend
(Env, File, Chained, plus graceful-degradation for AWS + Vault).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.core.credential_backends import (
    AWSSecretsBackend,
    ChainedBackend,
    EnvBackend,
    FileBackend,
    VaultBackend,
    backend_from_config,
)
from harness.core.credential_registry import (
    CredentialRegistry,
    CredentialRequirement,
    MissingCredentialError,
    ValidationReport,
)


# ── Registry: tool pack ingestion ─────────────────────────────────────────


class TestRegisterToolPack:
    def test_ingests_new_shape(self) -> None:
        pack = {
            "tools": [
                {
                    "name": "jira_search",
                    "credentials": [
                        {"name": "JIRA_EMAIL",     "purpose": "Atlassian email", "required": True},
                        {"name": "JIRA_API_TOKEN", "purpose": "API token"},
                    ],
                },
            ],
        }
        reg = CredentialRegistry()
        count = reg.register_tool_pack(pack)
        assert count == 1
        reqs = reg.required_for_tool("jira_search")
        names = [r.name for r in reqs]
        assert names == ["JIRA_EMAIL", "JIRA_API_TOKEN"]
        assert reqs[0].required is True
        assert reqs[1].required is True  # default

    def test_legacy_credential_source_alias(self) -> None:
        pack = {
            "tools": [
                {"name": "slack_post", "credential_source": "env:SLACK_BOT_TOKEN"},
            ],
        }
        reg = CredentialRegistry()
        count = reg.register_tool_pack(pack)
        assert count == 1
        reqs = reg.required_for_tool("slack_post")
        assert [r.name for r in reqs] == ["SLACK_BOT_TOKEN"]

    def test_skips_tools_without_name(self) -> None:
        pack = {"tools": [{"credentials": [{"name": "FOO"}]}]}
        reg = CredentialRegistry()
        assert reg.register_tool_pack(pack) == 0

    def test_idempotent_on_re_registration(self) -> None:
        reg = CredentialRegistry()
        reg.register_tool("jira_search", [{"name": "JIRA_API_TOKEN"}])
        reg.register_tool("jira_search", [{"name": "JIRA_API_TOKEN"}])
        assert len(reg.required_for_tool("jira_search")) == 1

    def test_declared_by_tracks_tool_name(self) -> None:
        reg = CredentialRegistry()
        reg.register_tool("jira_search", [{"name": "JIRA_API_TOKEN"}])
        req = reg.required_for_tool("jira_search")[0]
        assert req.declared_by == ("jira_search",)

    def test_pack_level_default_credentials_inherited(self) -> None:
        """Pack-level `credentials:` applies to every tool that doesn't override."""
        pack = {
            "id": "toolpack://services/jira",
            "credentials": [{"name": "JIRA_API_TOKEN"}, {"name": "JIRA_BASE_URL"}],
            "tools": [
                {"id": "tool://jira_search", "adapter_class": "http_api"},
                {"id": "tool://jira_create_issue", "adapter_class": "http_api"},
            ],
        }
        reg = CredentialRegistry()
        assert reg.register_tool_pack(pack) == 2
        for tool in ("jira_search", "jira_create_issue"):
            names = [r.name for r in reg.required_for_tool(tool)]
            assert names == ["JIRA_API_TOKEN", "JIRA_BASE_URL"]

    def test_default_policy_credential_source_legacy_shape(self) -> None:
        """Older packs put it under default_policy.credential_source: 'env:X'."""
        pack = {
            "id": "toolpack://services/slack",
            "default_policy": {"credential_source": "env:SLACK_BOT_TOKEN"},
            "tools": [
                {"id": "tool://slack_send_message", "adapter_class": "http_api"},
            ],
        }
        reg = CredentialRegistry()
        reg.register_tool_pack(pack)
        reqs = reg.required_for_tool("slack_send_message")
        assert [r.name for r in reqs] == ["SLACK_BOT_TOKEN"]

    def test_per_tool_overrides_pack_defaults(self) -> None:
        pack = {
            "credentials": [{"name": "PACK_DEFAULT"}],
            "tools": [
                {"id": "tool://special", "credentials": [{"name": "TOOL_SPECIFIC"}]},
                {"id": "tool://inherits"},
            ],
        }
        reg = CredentialRegistry()
        reg.register_tool_pack(pack)
        assert [r.name for r in reg.required_for_tool("special")] == ["TOOL_SPECIFIC"]
        assert [r.name for r in reg.required_for_tool("inherits")] == ["PACK_DEFAULT"]

    def test_tool_uri_and_short_name_both_indexed_as_short_name(self) -> None:
        """`id: tool://jira_search` indexes as `jira_search` so agents declaring either match."""
        pack = {
            "credentials": [{"name": "JIRA_API_TOKEN"}],
            "tools": [{"id": "tool://jira_search"}],
        }
        reg = CredentialRegistry()
        reg.register_tool_pack(pack)
        assert "jira_search" in reg.known_tools
        assert "tool://jira_search" not in reg.known_tools


# ── Registry: unions across tools + agents ────────────────────────────────


class TestUnionDerivation:
    def _registry_with_three_tools(self) -> CredentialRegistry:
        reg = CredentialRegistry()
        reg.register_tool("jira_search",       [{"name": "JIRA_API_TOKEN"}, {"name": "JIRA_BASE_URL"}])
        reg.register_tool("confluence_search", [{"name": "CONFLUENCE_API_TOKEN"}, {"name": "JIRA_BASE_URL"}])
        reg.register_tool("slack_post",        [{"name": "SLACK_BOT_TOKEN"}])
        return reg

    def test_dedupes_shared_credentials(self) -> None:
        reg = self._registry_with_three_tools()
        union = reg.required_for_tools(["jira_search", "confluence_search"])
        names = [r.name for r in union]
        assert names.count("JIRA_BASE_URL") == 1

    def test_merges_declared_by_on_shared_credentials(self) -> None:
        reg = self._registry_with_three_tools()
        union = reg.required_for_tools(["jira_search", "confluence_search"])
        shared = next(r for r in union if r.name == "JIRA_BASE_URL")
        assert set(shared.declared_by) == {"jira_search", "confluence_search"}

    def test_derive_from_agent_manifest_string_tools(self) -> None:
        reg = self._registry_with_three_tools()
        manifest = {"tools": ["jira_search", "slack_post"]}
        union = reg.required_for_agent(manifest)
        assert {r.name for r in union} == {"JIRA_API_TOKEN", "JIRA_BASE_URL", "SLACK_BOT_TOKEN"}

    def test_derive_from_agent_manifest_dict_tools(self) -> None:
        reg = self._registry_with_three_tools()
        manifest = {
            "tools": [
                {"name": "jira_search", "pack": "toolpack://services/jira"},
                {"name": "slack_post",  "pack": "toolpack://services/slack"},
            ],
        }
        union = reg.required_for_agent(manifest)
        assert {r.name for r in union} == {"JIRA_API_TOKEN", "JIRA_BASE_URL", "SLACK_BOT_TOKEN"}

    def test_unknown_tool_contributes_nothing(self) -> None:
        reg = self._registry_with_three_tools()
        union = reg.required_for_tools(["jira_search", "mystery_tool"])
        assert {r.name for r in union} == {"JIRA_API_TOKEN", "JIRA_BASE_URL"}


# ── Registry: validation + errors ─────────────────────────────────────────


class _StubBackend:
    """Backend that returns a fixed dict — no env/file fiddling in tests."""

    def __init__(self, values: dict[str, str | None]) -> None:
        self.values = values

    def resolve(self, name: str) -> str | None:
        value = self.values.get(name)
        return value if value else None


class TestValidate:
    def test_all_resolved_ok(self) -> None:
        reg = CredentialRegistry(backend=_StubBackend({"JIRA_API_TOKEN": "xxx"}))
        reg.register_tool("jira_search", [{"name": "JIRA_API_TOKEN"}])
        report = reg.validate(["jira_search"])
        assert report.ok
        assert [r.name for r in report.resolved] == ["JIRA_API_TOKEN"]
        assert report.missing == []

    def test_missing_required_reported(self) -> None:
        reg = CredentialRegistry(backend=_StubBackend({}))
        reg.register_tool("jira_search", [{"name": "JIRA_API_TOKEN", "purpose": "get one at atlassian"}])
        report = reg.validate(["jira_search"])
        assert not report.ok
        assert [r.name for r in report.missing] == ["JIRA_API_TOKEN"]

    def test_optional_missing_does_not_fail(self) -> None:
        reg = CredentialRegistry(backend=_StubBackend({}))
        reg.register_tool("jira_search", [{"name": "JIRA_EXTRA", "required": False}])
        report = reg.validate(["jira_search"])
        assert report.ok
        assert report.missing == []

    def test_raise_on_missing_raises(self) -> None:
        reg = CredentialRegistry(backend=_StubBackend({}))
        reg.register_tool("jira_search", [{"name": "JIRA_API_TOKEN", "purpose": "get token"}])
        with pytest.raises(MissingCredentialError) as exc:
            reg.validate(["jira_search"], agent_id="my-team/Foo/v1", raise_on_missing=True)
        msg = str(exc.value)
        assert "my-team/Foo/v1" in msg
        assert "JIRA_API_TOKEN" in msg
        assert "get token" in msg
        assert "CREDENTIALS_GUIDE" in msg

    def test_format_cli_shows_both(self) -> None:
        reg = CredentialRegistry(backend=_StubBackend({"A": "yes"}))
        reg.register_tool("tool_a", [{"name": "A"}])
        reg.register_tool("tool_b", [{"name": "B", "purpose": "needed for B"}])
        report = reg.validate(["tool_a", "tool_b"])
        rendered = report.format_cli()
        assert "1 credential(s) resolved" in rendered
        assert "1 credential(s) MISSING" in rendered
        assert "needed for B" in rendered


# ── Backends ──────────────────────────────────────────────────────────────


class TestEnvBackend:
    def test_reads_from_environ(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GENTCORE_TEST_CRED", "abc123")
        assert EnvBackend().resolve("GENTCORE_TEST_CRED") == "abc123"

    def test_empty_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GENTCORE_EMPTY", "")
        assert EnvBackend().resolve("GENTCORE_EMPTY") is None

    def test_missing_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GENTCORE_NO_SUCH", raising=False)
        assert EnvBackend().resolve("GENTCORE_NO_SUCH") is None


class TestFileBackend:
    def test_reads_json_file(self, tmp_path: Path) -> None:
        path = tmp_path / "creds.json"
        path.write_text(json.dumps({"JIRA_API_TOKEN": "secret-from-file"}), encoding="utf-8")
        backend = FileBackend(path=str(path))
        assert backend.resolve("JIRA_API_TOKEN") == "secret-from-file"

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        backend = FileBackend(path=str(tmp_path / "nope.json"))
        assert backend.resolve("ANYTHING") is None

    def test_missing_key_returns_none(self, tmp_path: Path) -> None:
        path = tmp_path / "creds.json"
        path.write_text('{"JIRA_API_TOKEN": "x"}', encoding="utf-8")
        assert FileBackend(path=str(path)).resolve("SLACK_BOT_TOKEN") is None

    def test_cached_after_first_load(self, tmp_path: Path) -> None:
        path = tmp_path / "creds.json"
        path.write_text('{"A": "one"}', encoding="utf-8")
        backend = FileBackend(path=str(path))
        assert backend.resolve("A") == "one"
        # Rewrite file — cache should NOT reflect the update within one backend lifetime
        path.write_text('{"A": "two"}', encoding="utf-8")
        assert backend.resolve("A") == "one"

    def test_invalid_json_returns_none(self, tmp_path: Path) -> None:
        path = tmp_path / "creds.json"
        path.write_text("not valid json", encoding="utf-8")
        assert FileBackend(path=str(path)).resolve("ANY") is None


class TestChainedBackend:
    def test_first_match_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SHARED_CRED", "from-env")
        chained = ChainedBackend([
            EnvBackend(),
            _StubBackend({"SHARED_CRED": "from-stub"}),
        ])
        assert chained.resolve("SHARED_CRED") == "from-env"

    def test_falls_through_to_second(self) -> None:
        chained = ChainedBackend([
            _StubBackend({}),
            _StubBackend({"X": "two"}),
        ])
        assert chained.resolve("X") == "two"

    def test_all_miss_returns_none(self) -> None:
        chained = ChainedBackend([_StubBackend({}), _StubBackend({})])
        assert chained.resolve("X") is None


class TestAWSSecretsGracefulDegradation:
    def test_returns_none_when_boto3_missing(self) -> None:
        """When boto3 isn't installed, backend must fail closed, not crash."""
        backend = AWSSecretsBackend(prefix="gentcore/test/")
        assert backend.resolve("JIRA_API_TOKEN") is None


class TestVaultGracefulDegradation:
    def test_returns_none_when_hvac_missing(self) -> None:
        backend = VaultBackend()
        assert backend.resolve("JIRA_API_TOKEN") is None


class TestBackendFactory:
    def test_none_config_returns_env(self) -> None:
        assert isinstance(backend_from_config(None), EnvBackend)

    def test_empty_backends_returns_env(self) -> None:
        assert isinstance(backend_from_config({"backends": []}), EnvBackend)

    def test_single_env_returns_env(self) -> None:
        backend = backend_from_config({"backends": [{"type": "env"}]})
        assert isinstance(backend, EnvBackend)

    def test_multiple_returns_chained(self, tmp_path: Path) -> None:
        backend = backend_from_config({
            "backends": [
                {"type": "env"},
                {"type": "file", "path": str(tmp_path / "creds.json")},
            ],
        })
        assert isinstance(backend, ChainedBackend)
        assert len(backend.backends) == 2

    def test_unknown_type_is_skipped(self) -> None:
        backend = backend_from_config({"backends": [{"type": "bogus"}, {"type": "env"}]})
        assert isinstance(backend, EnvBackend)

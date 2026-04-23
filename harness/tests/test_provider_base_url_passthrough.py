"""Verify provider.yaml `base_url:` is passed through to AnthropicProvider.

Prior to this coverage, `base_url` was honoured only for the OpenAI branch;
the Anthropic branch in both the CLI (`harness/cli/ai.py::_make_provider`)
and the server (`harness/server/runner.py::_make_provider`) silently dropped
it. Running GLM/MiniMax/other Anthropic-compat vendors then hit
api.anthropic.com with the vendor key → 401.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def _anthropic_provider_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Write a provider.yaml that points at an Anthropic-compat vendor URL."""
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "provider.yaml").write_text(
        "provider: anthropic\n"
        "model: glm-4.6\n"
        "max_tokens: 8192\n"
        "api_key_env: ZHIPU_API_KEY\n"
        "base_url: https://open.bigmodel.cn/api/anthropic\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ZHIPU_API_KEY", "sk-test-glm-key")
    return tmp_path / "config" / "provider.yaml"


class TestCliProviderFactoryPassesBaseUrl:
    """The CLI's _make_provider builds the provider from a resolved yaml path."""

    def test_base_url_forwarded_to_anthropic_provider(
        self, _anthropic_provider_yaml: Path,
    ) -> None:
        from harness.cli import ai as cli

        captured: dict = {}

        def _fake_ctor(**kwargs):
            captured.update(kwargs)

            class _Stub:
                def __init__(self) -> None:
                    self.provider_name = "AnthropicStub"

            return _Stub()

        with patch("harness.providers.anthropic_provider.AnthropicProvider", side_effect=_fake_ctor):
            cli._make_provider(dry_run=False, provider_config_path=str(_anthropic_provider_yaml))

        assert captured.get("base_url") == "https://open.bigmodel.cn/api/anthropic"
        assert captured.get("api_key") == "sk-test-glm-key"
        assert captured.get("model") == "glm-4.6"

    def test_env_override_still_wins_when_provider_yaml_omits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If provider.yaml has no base_url, ANTHROPIC_BASE_URL env still applies."""
        (tmp_path / "config").mkdir()
        (tmp_path / "config" / "provider.yaml").write_text(
            "provider: anthropic\nmodel: glm-4.6\napi_key_env: ZHIPU_API_KEY\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("ZHIPU_API_KEY", "sk-test")
        monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://env-override.example/api")

        from harness.cli import ai as cli

        captured: dict = {}

        def _fake_ctor(**kwargs):
            captured.update(kwargs)

            class _Stub:
                pass

            return _Stub()

        with patch("harness.providers.anthropic_provider.AnthropicProvider", side_effect=_fake_ctor):
            cli._make_provider(
                dry_run=False,
                provider_config_path=str(tmp_path / "config" / "provider.yaml"),
            )

        assert captured.get("base_url") == "https://env-override.example/api"


class TestServerProviderFactoryPassesBaseUrl:
    """harness.server.runner._make_provider applies the same pass-through
    with an extra egress_guard check (SSRF defense)."""

    def test_server_forwards_base_url_to_anthropic(
        self, _anthropic_provider_yaml: Path,
    ) -> None:
        from harness.server import runner as server_runner

        captured: dict = {}

        def _fake_ctor(**kwargs):
            captured.update(kwargs)

            class _Stub:
                pass

            return _Stub()

        domain_path = str(_anthropic_provider_yaml.parent.parent)
        with patch("harness.providers.anthropic_provider.AnthropicProvider", side_effect=_fake_ctor):
            server_runner._make_provider(domain_path, dry_run=False)

        assert captured.get("base_url") == "https://open.bigmodel.cn/api/anthropic"
        assert captured.get("api_key") == "sk-test-glm-key"

    def test_server_rejects_internal_base_url(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """SSRF defence — a provider.yaml pointing at 169.254.169.254 is blocked."""
        (tmp_path / "config").mkdir()
        (tmp_path / "config" / "provider.yaml").write_text(
            "provider: anthropic\n"
            "model: glm-4.6\n"
            "api_key_env: ZHIPU_API_KEY\n"
            "base_url: http://169.254.169.254/latest/meta-data/\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("ZHIPU_API_KEY", "sk-test")

        from harness.server import runner as server_runner

        with pytest.raises(ValueError, match="Disallowed base_url"):
            server_runner._make_provider(str(tmp_path), dry_run=False)


class TestServerAllowlistExpansion:
    """The vendor API key env vars (ZHIPU, GLM, MiniMax, Gemini) should be
    on the server allowlist so domain.yaml can legitimately reference them."""

    def test_zhipu_is_allowed(self) -> None:
        from harness.server.runner import _ALLOWED_ENV_VARS
        assert "ZHIPU_API_KEY" in _ALLOWED_ENV_VARS

    def test_glm_is_allowed(self) -> None:
        from harness.server.runner import _ALLOWED_ENV_VARS
        assert "GLM_API_KEY" in _ALLOWED_ENV_VARS

    def test_minimax_is_allowed(self) -> None:
        from harness.server.runner import _ALLOWED_ENV_VARS
        assert "MINIMAX_API_KEY" in _ALLOWED_ENV_VARS

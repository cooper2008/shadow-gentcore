"""Security tests for harness/server/runner.py — PR 1.2 + PR 1.3.

Covers:
  - _validate_domain_path: traversal prevention via ALLOWED_DOMAIN_ROOTS
  - _make_provider: api_key_env allowlist enforcement
  - _make_provider: unknown provider explicit failure (no silent fallback)
  - _validate_agent_id: agent ID format validation (path traversal prevention)
  - _resolve_agent_dir: resolved path containment check
"""

from __future__ import annotations

import os
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# _validate_domain_path
# ---------------------------------------------------------------------------

class TestValidateDomainPath:
    """Tests for filesystem-traversal prevention."""

    def _fn(self, domain_path: str) -> Path:
        from harness.server.runner import _validate_domain_path
        return _validate_domain_path(domain_path)

    def test_normal_path_accepted_without_roots(self, tmp_path: Path) -> None:
        """When ALLOWED_DOMAIN_ROOTS is unset, any path is accepted (backward compat)."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ALLOWED_DOMAIN_ROOTS", None)
            result = self._fn(str(tmp_path))
        assert result == tmp_path.resolve()

    def test_path_inside_allowed_root_accepted(self, tmp_path: Path) -> None:
        sub = tmp_path / "projects" / "my-domain"
        sub.mkdir(parents=True)
        with patch.dict(os.environ, {"ALLOWED_DOMAIN_ROOTS": str(tmp_path)}):
            result = self._fn(str(sub))
        assert result == sub.resolve()

    def test_traversal_rejected_when_roots_set(self, tmp_path: Path) -> None:
        allowed = tmp_path / "safe"
        allowed.mkdir()
        evil = tmp_path / "evil"
        evil.mkdir()
        with patch.dict(os.environ, {"ALLOWED_DOMAIN_ROOTS": str(allowed)}):
            with pytest.raises(ValueError, match="outside allowed roots"):
                self._fn(str(evil))

    def test_dotdot_traversal_rejected(self, tmp_path: Path) -> None:
        allowed = tmp_path / "safe"
        allowed.mkdir()
        traversal = str(allowed / ".." / "evil")
        with patch.dict(os.environ, {"ALLOWED_DOMAIN_ROOTS": str(allowed)}):
            with pytest.raises(ValueError, match="outside allowed roots"):
                self._fn(traversal)

    def test_multiple_allowed_roots(self, tmp_path: Path) -> None:
        root_a = tmp_path / "a"
        root_b = tmp_path / "b"
        root_a.mkdir()
        root_b.mkdir()
        roots = f"{root_a}:{root_b}"
        with patch.dict(os.environ, {"ALLOWED_DOMAIN_ROOTS": roots}):
            assert self._fn(str(root_a)) == root_a.resolve()
            assert self._fn(str(root_b)) == root_b.resolve()


# ---------------------------------------------------------------------------
# _make_provider — env var allowlist
# ---------------------------------------------------------------------------

class TestMakeProviderEnvAllowlist:
    """Tests for api_key_env allowlist enforcement."""

    def _write_provider_yaml(self, domain: Path, content: str) -> None:
        cfg_dir = domain / "config"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "provider.yaml").write_text(textwrap.dedent(content), encoding="utf-8")

    def test_allowed_env_var_accepted(self, tmp_path: Path) -> None:
        from harness.server.runner import _make_provider

        self._write_provider_yaml(tmp_path, """\
            provider: anthropic
            api_key_env: ANTHROPIC_API_KEY
        """)
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}):
            provider = _make_provider(str(tmp_path), dry_run=False)
        assert provider is not None

    def test_disallowed_env_var_rejected(self, tmp_path: Path) -> None:
        from harness.server.runner import _make_provider

        self._write_provider_yaml(tmp_path, """\
            provider: anthropic
            api_key_env: AWS_SECRET_ACCESS_KEY
        """)
        with pytest.raises(ValueError, match="Disallowed api_key_env"):
            _make_provider(str(tmp_path), dry_run=False)

    def test_arbitrary_env_var_rejected(self, tmp_path: Path) -> None:
        from harness.server.runner import _make_provider

        self._write_provider_yaml(tmp_path, """\
            provider: anthropic
            api_key_env: DATABASE_PASSWORD
        """)
        with pytest.raises(ValueError, match="Disallowed api_key_env"):
            _make_provider(str(tmp_path), dry_run=False)


# ---------------------------------------------------------------------------
# _make_provider — unknown provider
# ---------------------------------------------------------------------------

class TestMakeProviderUnknown:
    """Tests for unknown provider handling."""

    def _write_provider_yaml(self, domain: Path, content: str) -> None:
        cfg_dir = domain / "config"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "provider.yaml").write_text(textwrap.dedent(content), encoding="utf-8")

    def test_unknown_provider_raises_when_not_dry_run(self, tmp_path: Path) -> None:
        from harness.server.runner import _make_provider

        self._write_provider_yaml(tmp_path, """\
            provider: some_unknown_llm
            api_key_env: ANTHROPIC_API_KEY
        """)
        with pytest.raises(ValueError, match="Unknown provider: some_unknown_llm"):
            _make_provider(str(tmp_path), dry_run=False)

    def test_dry_run_returns_dry_run_provider_regardless(self, tmp_path: Path) -> None:
        from harness.providers.dry_run import DryRunProvider
        from harness.server.runner import _make_provider

        self._write_provider_yaml(tmp_path, """\
            provider: some_unknown_llm
        """)
        provider = _make_provider(str(tmp_path), dry_run=True)
        assert isinstance(provider, DryRunProvider)


# ---------------------------------------------------------------------------
# _validate_agent_id — PR 1.3
# ---------------------------------------------------------------------------

class TestValidateAgentId:
    """Tests for agent_id format validation (path traversal prevention)."""

    def _fn(self, agent_id: str) -> None:
        from harness.server.runner import _validate_agent_id
        _validate_agent_id(agent_id)

    def test_two_part_id_valid(self) -> None:
        """Standard two-part ID like 'AgentName/v1' is accepted."""
        self._fn("AgentName/v1")  # should not raise

    def test_three_part_id_valid(self) -> None:
        """Three-part ID like '_genesis/SourceScannerAgent/v1' is accepted."""
        self._fn("_genesis/SourceScannerAgent/v1")  # should not raise

    def test_hyphens_and_underscores_valid(self) -> None:
        """Hyphens and underscores are allowed in segments."""
        self._fn("my-category/My_Agent/v1-beta")  # should not raise

    def test_dotdot_traversal_rejected(self) -> None:
        """Path traversal with '..' is rejected."""
        with pytest.raises(ValueError, match="Invalid agent ID format"):
            self._fn("../../../etc/passwd")

    def test_dotdot_in_middle_rejected(self) -> None:
        """Path traversal with '..' embedded in segments is rejected."""
        with pytest.raises(ValueError, match="Invalid agent ID format"):
            self._fn("AgentName/../../../v1")

    def test_spaces_rejected(self) -> None:
        """Spaces in agent ID are rejected."""
        with pytest.raises(ValueError, match="Invalid agent ID format"):
            self._fn("Agent Name/v1")

    def test_empty_string_rejected(self) -> None:
        """Empty string is rejected."""
        with pytest.raises(ValueError, match="Invalid agent ID format"):
            self._fn("")

    def test_single_segment_rejected(self) -> None:
        """Single segment without a slash is rejected (needs at least 2 parts)."""
        with pytest.raises(ValueError, match="Invalid agent ID format"):
            self._fn("AgentName")

    def test_four_segments_rejected(self) -> None:
        """Four or more segments are rejected."""
        with pytest.raises(ValueError, match="Invalid agent ID format"):
            self._fn("a/b/c/d")

    def test_special_chars_rejected(self) -> None:
        """Special characters like semicolons or backticks are rejected."""
        with pytest.raises(ValueError, match="Invalid agent ID format"):
            self._fn("agent;rm -rf/v1")


# ---------------------------------------------------------------------------
# _resolve_agent_dir containment — PR 1.3
# ---------------------------------------------------------------------------

class TestResolveAgentDirContainment:
    """Tests for _resolve_agent_dir path containment verification."""

    def test_valid_two_part_resolves(self, tmp_path: Path) -> None:
        """A valid two-part agent ID resolves when the directory exists."""
        from harness.server.runner import _resolve_agent_dir

        agent_dir = tmp_path / "agents" / "MyAgent" / "v1"
        agent_dir.mkdir(parents=True)
        result = _resolve_agent_dir("MyAgent/v1", tmp_path)
        assert result is not None
        assert result.exists()

    def test_valid_three_part_resolves(self, tmp_path: Path) -> None:
        """A valid three-part agent ID resolves when the directory exists."""
        from harness.server.runner import _resolve_agent_dir

        agent_dir = tmp_path / "agents" / "Scanner" / "v1"
        agent_dir.mkdir(parents=True)
        result = _resolve_agent_dir("_genesis/Scanner/v1", tmp_path)
        assert result is not None
        assert result.exists()

    def test_nonexistent_agent_returns_none(self, tmp_path: Path) -> None:
        """An agent ID that points to a non-existent directory returns None."""
        from harness.server.runner import _resolve_agent_dir

        result = _resolve_agent_dir("NoSuchAgent/v1", tmp_path)
        assert result is None

    def test_traversal_agent_id_raises(self) -> None:
        """A traversal agent_id is rejected before path resolution."""
        from harness.server.runner import _resolve_agent_dir

        with pytest.raises(ValueError, match="Invalid agent ID format"):
            _resolve_agent_dir("../../etc/passwd", Path("/tmp"))

    def test_containment_rejects_symlink_escape(self, tmp_path: Path) -> None:
        """If a symlink causes the resolved path to escape the root, return None."""
        from harness.server.runner import _resolve_agent_dir

        # Create a directory outside the domain root
        outside = tmp_path / "outside"
        outside.mkdir()

        # Create agents dir structure with a symlink that escapes
        domain_root = tmp_path / "domain"
        agents_dir = domain_root / "agents" / "EvilAgent"
        agents_dir.mkdir(parents=True)
        # Symlink v1 -> ../../outside (escapes domain_root)
        (agents_dir / "v1").symlink_to(outside)

        result = _resolve_agent_dir("EvilAgent/v1", domain_root)
        assert result is None

"""Tests for GENTCORE_STRICT_MANIFESTS — fail-fast manifest validation.

Default behavior keeps the historical warning-only path so dev domains
with looser manifests keep loading. With ``GENTCORE_STRICT_MANIFESTS=1``
the loader raises ``ValueError`` instead — production HTTP startup, CI,
and pre-commit hooks should set this flag.

Coverage spans the three validation sites in ``ManifestLoader``:
  * ``load_domain``  — domain.yaml schema
  * ``load_agent``   — agent_manifest.yaml schema
  * ``load_workflow`` — workflows/*.yaml schema

Plus the dispatcher helper, the env-var parsing, and the cause-chain
preservation that lets operators see the underlying pydantic error.
"""

from __future__ import annotations

import tempfile
import warnings
from pathlib import Path

import pytest

from harness.core.manifest_loader import (
    ManifestLoader,
    _emit_manifest_validation_failure,
    _strict_manifests_enabled,
)


class TestStrictModeFlag:
    def test_default_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GENTCORE_STRICT_MANIFESTS", raising=False)
        assert _strict_manifests_enabled() is False

    @pytest.mark.parametrize("v", ["1", "true", "yes", "on", "TRUE", "True", "  on  "])
    def test_truthy_values_enable(self, monkeypatch: pytest.MonkeyPatch, v: str) -> None:
        monkeypatch.setenv("GENTCORE_STRICT_MANIFESTS", v)
        assert _strict_manifests_enabled() is True

    @pytest.mark.parametrize("v", ["0", "false", "no", "off", ""])
    def test_falsy_values_disable(self, monkeypatch: pytest.MonkeyPatch, v: str) -> None:
        monkeypatch.setenv("GENTCORE_STRICT_MANIFESTS", v)
        assert _strict_manifests_enabled() is False


class TestDispatcher:
    def test_off_emits_warning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GENTCORE_STRICT_MANIFESTS", raising=False)
        underlying = ValueError("bad field x")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _emit_manifest_validation_failure("agent", "/x/y/agent_manifest.yaml", underlying)
        assert len(caught) == 1
        assert "Agent manifest validation failed" in str(caught[0].message)
        assert "/x/y/agent_manifest.yaml" in str(caught[0].message)

    def test_on_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GENTCORE_STRICT_MANIFESTS", "1")
        underlying = ValueError("bad field x")
        with pytest.raises(ValueError) as exc_info:
            _emit_manifest_validation_failure("workflow", "/a/b/main.yaml", underlying)
        assert "Workflow manifest validation failed" in str(exc_info.value)
        assert exc_info.value.__cause__ is underlying

    def test_kind_capitalized_in_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GENTCORE_STRICT_MANIFESTS", "1")
        for kind in ("domain", "agent", "workflow"):
            with pytest.raises(ValueError) as exc_info:
                _emit_manifest_validation_failure(kind, "x", RuntimeError("e"))
            assert kind.capitalize() in str(exc_info.value)


@pytest.fixture
def domain_root() -> Any:  # type: ignore[name-defined]
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


class TestEndToEndDomain:
    def test_invalid_domain_warns_in_default_mode(
        self, domain_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("GENTCORE_STRICT_MANIFESTS", raising=False)
        # missing required `name` field
        (domain_root / "domain.yaml").write_text("industry: backend\n", encoding="utf-8")
        loader = ManifestLoader()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            data = loader.load_domain(domain_root)
        assert any("Domain manifest validation failed" in str(w.message) for w in caught)
        assert data == {"industry": "backend"}  # still returns parsed yaml

    def test_invalid_domain_raises_in_strict_mode(
        self, domain_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GENTCORE_STRICT_MANIFESTS", "1")
        (domain_root / "domain.yaml").write_text("industry: backend\n", encoding="utf-8")
        loader = ManifestLoader()
        with pytest.raises(ValueError, match="Domain manifest validation failed"):
            loader.load_domain(domain_root)

    def test_valid_domain_passes_in_strict_mode(
        self, domain_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GENTCORE_STRICT_MANIFESTS", "1")
        (domain_root / "domain.yaml").write_text(
            "name: test_domain\nindustry: backend\nowner: t\npurpose: smoke\n",
            encoding="utf-8",
        )
        loader = ManifestLoader()
        data = loader.load_domain(domain_root)
        assert data["name"] == "test_domain"


class TestEndToEndAgent:
    def test_invalid_agent_raises_in_strict_mode(
        self, domain_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GENTCORE_STRICT_MANIFESTS", "1")
        agent_dir = domain_root / "agents" / "Foo" / "v1"
        agent_dir.mkdir(parents=True)
        # missing required fields (id, domain, category, system_prompt_ref)
        (agent_dir / "agent_manifest.yaml").write_text("version: 1.0.0\n", encoding="utf-8")
        loader = ManifestLoader()
        with pytest.raises(ValueError, match="Agent manifest validation failed"):
            loader.load_agent(agent_dir, domain_root)


class TestEndToEndWorkflow:
    def test_invalid_workflow_raises_in_strict_mode(
        self, domain_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GENTCORE_STRICT_MANIFESTS", "1")
        wf = domain_root / "workflows" / "main.yaml"
        wf.parent.mkdir(parents=True)
        # Missing required fields per WorkflowDefinition schema
        wf.write_text("steps: []\n", encoding="utf-8")
        loader = ManifestLoader()
        with pytest.raises(ValueError, match="Workflow manifest validation failed"):
            loader.load_workflow(wf)


# Late import to satisfy the fixture's `Any` annotation.
from typing import Any  # noqa: E402

"""Tests for configuration file loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class TestCategoriesConfig:
    """Tests for config/categories.yaml."""

    def test_file_exists(self) -> None:
        path = REPO_ROOT / "config" / "categories.yaml"
        assert path.exists(), "config/categories.yaml must exist"

    def test_loads_valid_yaml(self) -> None:
        path = REPO_ROOT / "config" / "categories.yaml"
        data = yaml.safe_load(path.read_text())
        assert "categories" in data

    def test_has_required_categories(self) -> None:
        path = REPO_ROOT / "config" / "categories.yaml"
        data = yaml.safe_load(path.read_text())
        categories = data["categories"]
        for name in ("reasoning", "fast-codegen", "security-analysis", "cost-optimized"):
            assert name in categories, f"Missing category: {name}"

    def test_category_has_required_fields(self) -> None:
        path = REPO_ROOT / "config" / "categories.yaml"
        data = yaml.safe_load(path.read_text())
        for name, config in data["categories"].items():
            assert "provider" in config, f"{name} missing 'provider'"
            assert "model" in config, f"{name} missing 'model'"
            assert "temperature" in config, f"{name} missing 'temperature'"


class TestDomainsConfig:
    """Tests for config/domains.yaml."""

    def test_file_exists(self) -> None:
        path = REPO_ROOT / "config" / "domains.yaml"
        assert path.exists(), "config/domains.yaml must exist"

    def test_loads_valid_yaml(self) -> None:
        path = REPO_ROOT / "config" / "domains.yaml"
        data = yaml.safe_load(path.read_text())
        assert "discovery" in data

    def test_has_paths_entry(self) -> None:
        path = REPO_ROOT / "config" / "domains.yaml"
        data = yaml.safe_load(path.read_text())
        assert "paths" in data["discovery"]
        assert isinstance(data["discovery"]["paths"], list)
        assert len(data["discovery"]["paths"]) > 0


class TestEnvironmentConfigs:
    """Tests for config/environments/ local and cloud configs."""

    @pytest.mark.parametrize("env_name", ["local", "cloud"])
    def test_file_exists(self, env_name: str) -> None:
        path = REPO_ROOT / "config" / "environments" / f"{env_name}.yaml"
        assert path.exists(), f"config/environments/{env_name}.yaml must exist"

    @pytest.mark.parametrize("env_name", ["local", "cloud"])
    def test_loads_valid_yaml(self, env_name: str) -> None:
        path = REPO_ROOT / "config" / "environments" / f"{env_name}.yaml"
        data = yaml.safe_load(path.read_text())
        assert data is not None

    @pytest.mark.parametrize("env_name", ["local", "cloud"])
    def test_has_required_keys(self, env_name: str) -> None:
        path = REPO_ROOT / "config" / "environments" / f"{env_name}.yaml"
        data = yaml.safe_load(path.read_text())
        for key in ("runtime", "permissions", "workspace", "storage", "credentials", "output", "budget"):
            assert key in data, f"{env_name}.yaml missing required key: {key}"

    def test_local_is_interactive(self) -> None:
        path = REPO_ROOT / "config" / "environments" / "local.yaml"
        data = yaml.safe_load(path.read_text())
        assert data["permissions"]["mode"] == "interactive"

    def test_cloud_is_non_interactive(self) -> None:
        path = REPO_ROOT / "config" / "environments" / "cloud.yaml"
        data = yaml.safe_load(path.read_text())
        assert data["permissions"]["mode"] == "non_interactive"
        assert data["permissions"]["default_action"] == "deny"

"""Tests for the CLI entry point."""

from __future__ import annotations

from click.testing import CliRunner

from harness.cli.ai import cli


class TestCLI:
    """Tests for the ./ai CLI."""

    def test_help_exits_zero(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "Multi-domain AI agent framework CLI" in result.output

    def test_version(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "0.1.0" in result.output

    def test_subcommands_listed(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        for cmd in ("domain", "pack", "agent", "workflow", "validate", "certify", "publish", "run"):
            assert cmd in result.output, f"Missing subcommand: {cmd}"

    def test_domain_init(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(cli, ["domain", "init", "test_domain"])
            assert result.exit_code == 0, result.output
            assert "test_domain" in result.output
            assert "Scaffolded" in result.output

    def test_pack_create(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            runner.invoke(cli, ["domain", "init", "backend"])
            result = runner.invoke(cli, ["pack", "create", "backend", "build"])
            assert result.exit_code == 0, result.output
            assert "build" in result.output

    def test_agent_create(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            runner.invoke(cli, ["domain", "init", "backend"])
            result = runner.invoke(cli, ["agent", "create", "backend", "CodeGenAgent"])
            assert result.exit_code == 0, result.output
            assert "CodeGenAgent" in result.output

    def test_workflow_create(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            runner.invoke(cli, ["domain", "init", "backend"])
            result = runner.invoke(cli, ["workflow", "create", "backend", "quick_change"])
            assert result.exit_code == 0, result.output
            assert "quick_change" in result.output

    def test_validate_good_domain(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            runner.invoke(cli, ["domain", "init", "mydom"])
            result = runner.invoke(cli, ["validate", "mydom"])
            assert result.exit_code == 0, result.output
            assert "PASS" in result.output

    def test_validate_bad_domain(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            import os
            os.makedirs("empty")
            result = runner.invoke(cli, ["validate", "empty"])
            assert result.exit_code != 0

    def test_certify_good_domain(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            runner.invoke(cli, ["domain", "init", "mydom"])
            result = runner.invoke(cli, ["certify", "mydom"])
            assert result.exit_code == 0, result.output
            assert "CERTIFIED" in result.output

    def test_publish_domain(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            runner.invoke(cli, ["domain", "init", "mydom"])
            result = runner.invoke(cli, ["publish", "mydom", "--version", "1.0.0"])
            assert result.exit_code == 0, result.output
            assert "Published" in result.output


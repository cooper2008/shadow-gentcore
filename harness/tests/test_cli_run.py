"""Tests for CLI run commands — dry-run mode (no LLM needed)."""

from __future__ import annotations

from click.testing import CliRunner

from harness.cli.ai import cli


class TestCliRunAgent:
    def test_missing_domain_fails(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, [
            "run", "agent", "nonexistent/Agent/v1",
            "--task", "test",
            "--domain", "/nonexistent/path",
            "--dry-run",
        ])
        assert result.exit_code != 0

    def test_missing_api_key_falls_back_or_fails(self) -> None:
        """Without ANTHROPIC_API_KEY, _make_provider falls back to ClaudeCodeProvider
        if claude CLI is available, or fails if not. Either behavior is acceptable."""
        runner = CliRunner()
        result = runner.invoke(cli, [
            "run", "agent", "some_domain/SomeAgent/v1",
            "--task", "test",
            "--domain", "/nonexistent/path",
        ], env={"ANTHROPIC_API_KEY": "", "PATH": ""})
        # With no API key AND no claude CLI in PATH, should fail
        assert result.exit_code != 0


class TestCliRunWorkflow:
    def test_missing_workflow_fails(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, [
            "run", "workflow", "/nonexistent/workflow.yaml", "--dry-run",
        ])
        assert result.exit_code != 0


class TestCliToolCommands:
    def test_tool_list(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["tool", "list"])
        assert result.exit_code == 0
        # Should list at least core packs
        assert "toolpack://" in result.output

    def test_tool_add_github(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["tool", "add", "github"])
        assert result.exit_code == 0
        assert "github" in result.output.lower()

    def test_tool_add_nonexistent_fails(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["tool", "add", "nonexistent_tool_xyz"])
        assert result.exit_code != 0

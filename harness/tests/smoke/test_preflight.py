"""Unit tests for PreflightCheck — 4-repo verification."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from harness.tests.smoke.preflight import PreflightCheck, CheckLevel, PreflightReport


PROJECT_ROOT = Path(__file__).resolve().parents[3]


class TestPreflightDetectsInstalledRepos:
    """Verify pre-flight detects properly installed repos."""

    def test_preflight_all_pass_in_dev_env(self):
        """In a properly set up dev environment, all checks should pass or info."""
        report = PreflightCheck(PROJECT_ROOT).check_all()
        # Should not have fatals in a working dev env
        # (agent-contracts and agent-tools may be SKIP if not installed)
        for r in report.results:
            if r.level == CheckLevel.FATAL:
                pytest.fail(f"Fatal check failed: {r.name}: {r.message}")

    def test_python_version_passes(self):
        report = PreflightCheck(PROJECT_ROOT).check_all()
        py_check = next(r for r in report.results if r.name == "python_version")
        assert py_check.level == CheckLevel.PASS

    def test_harness_package_passes(self):
        report = PreflightCheck(PROJECT_ROOT).check_all()
        harness_check = next(r for r in report.results if r.name == "package_harness")
        assert harness_check.level == CheckLevel.PASS

    def test_genesis_agents_detected(self):
        report = PreflightCheck(PROJECT_ROOT).check_all()
        genesis_check = next(r for r in report.results if r.name == "genesis_agents")
        assert genesis_check.level == CheckLevel.PASS
        assert "8" in genesis_check.message or "agents" in genesis_check.message.lower()

    def test_shared_agents_detected(self):
        report = PreflightCheck(PROJECT_ROOT).check_all()
        shared_check = next(r for r in report.results if r.name == "shared_agents")
        assert shared_check.level == CheckLevel.PASS


class TestPreflightHandlesMissingRepos:
    """Verify pre-flight gracefully handles missing repos."""

    def test_preflight_handles_missing_agent_tools(self):
        """When agent_tools is not importable, should SKIP not FATAL."""
        checker = PreflightCheck(PROJECT_ROOT)
        with patch("importlib.import_module", side_effect=ImportError("mocked")):
            result = checker._check_package("agent_tools", fatal=False)
        assert result.level == CheckLevel.SKIP

    def test_preflight_handles_missing_fatal_package(self):
        """Fatal packages should return FATAL when missing."""
        checker = PreflightCheck(PROJECT_ROOT)
        with patch("importlib.import_module", side_effect=ImportError("mocked")):
            result = checker._check_package("nonexistent_pkg", fatal=True)
        assert result.level == CheckLevel.FATAL

    def test_preflight_detects_missing_genesis_agents(self, tmp_path):
        """When genesis dir doesn't exist, should be FATAL."""
        checker = PreflightCheck(tmp_path)
        result = checker._check_genesis_agents()
        assert result.level == CheckLevel.FATAL

    def test_preflight_detects_missing_shared_agents(self, tmp_path):
        """When shared dir doesn't exist, should be FATAL."""
        checker = PreflightCheck(tmp_path)
        result = checker._check_shared_agents()
        assert result.level == CheckLevel.FATAL


class TestPreflightReportStructure:
    """Verify PreflightReport dataclass behavior."""

    def test_empty_report_no_fatal(self):
        report = PreflightReport()
        assert not report.has_fatal
        assert report.passed_count == 0
        assert report.total_count == 0

    def test_report_with_fatal(self):
        from harness.tests.smoke.preflight import CheckResult
        report = PreflightReport(results=[
            CheckResult("check1", CheckLevel.PASS, "ok"),
            CheckResult("check2", CheckLevel.FATAL, "broken"),
        ])
        assert report.has_fatal
        assert report.passed_count == 1
        assert report.total_count == 2
        assert "check2" in report.fatal_summary

    def test_report_summary_lines(self):
        from harness.tests.smoke.preflight import CheckResult
        report = PreflightReport(results=[
            CheckResult("py", CheckLevel.PASS, "Python 3.12"),
            CheckResult("tools", CheckLevel.WARN, "optional"),
        ])
        lines = report.summary_lines()
        assert len(lines) == 2
        assert "✓" in lines[0]
        assert "⚠" in lines[1]

    def test_report_skip_summary(self):
        from harness.tests.smoke.preflight import CheckResult
        report = PreflightReport(results=[
            CheckResult("a", CheckLevel.SKIP, "skipped reason"),
        ])
        assert report.has_skip
        assert "skipped reason" in report.skip_summary

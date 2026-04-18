"""Pre-flight verifier — checks all 4 repos are installed and accessible before smoke tests.

Usage:
    from harness.tests.smoke.preflight import PreflightCheck

    report = PreflightCheck().check_all()
    if report.has_fatal:
        sys.exit(f"Pre-flight failed: {report.fatal_summary}")
"""

from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class CheckLevel(str, Enum):
    """Severity level for pre-flight check results."""
    PASS = "pass"
    INFO = "info"
    WARN = "warn"
    SKIP = "skip"
    FATAL = "fatal"


@dataclass
class CheckResult:
    """Result of a single pre-flight check."""
    name: str
    level: CheckLevel
    message: str
    detail: str = ""


@dataclass
class PreflightReport:
    """Aggregate report from all pre-flight checks."""
    results: list[CheckResult] = field(default_factory=list)

    @property
    def has_fatal(self) -> bool:
        return any(r.level == CheckLevel.FATAL for r in self.results)

    @property
    def has_skip(self) -> bool:
        return any(r.level == CheckLevel.SKIP for r in self.results)

    @property
    def fatal_summary(self) -> str:
        fatals = [r for r in self.results if r.level == CheckLevel.FATAL]
        return "; ".join(f"{r.name}: {r.message}" for r in fatals)

    @property
    def skip_summary(self) -> str:
        skips = [r for r in self.results if r.level == CheckLevel.SKIP]
        return "; ".join(f"{r.name}: {r.message}" for r in skips)

    @property
    def passed_count(self) -> int:
        return sum(1 for r in self.results if r.level == CheckLevel.PASS)

    @property
    def total_count(self) -> int:
        return len(self.results)

    def summary_lines(self) -> list[str]:
        """Return a human-readable summary for CLI output."""
        lines: list[str] = []
        icons = {
            CheckLevel.PASS: "✓",
            CheckLevel.INFO: "ℹ",
            CheckLevel.WARN: "⚠",
            CheckLevel.SKIP: "⊘",
            CheckLevel.FATAL: "✗",
        }
        for r in self.results:
            icon = icons.get(r.level, "?")
            line = f"  {icon} [{r.level.value:5s}] {r.name}: {r.message}"
            lines.append(line)
            if r.detail:
                lines.append(f"           {r.detail}")
        return lines


# ── Pre-flight project root ────────────────────────────────────────────────

def _project_root() -> Path:
    """Resolve shadow-gentcore project root from this file's location."""
    return Path(__file__).resolve().parents[3]


# ── PreflightCheck ─────────────────────────────────────────────────────────

class PreflightCheck:
    """Verifies all 4 repos are installed and accessible before smoke tests."""

    def __init__(self, project_root: Path | None = None) -> None:
        self.project_root = project_root or _project_root()

    def check_all(self) -> PreflightReport:
        """Run all pre-flight checks and return an aggregate report."""
        report = PreflightReport()

        report.results.append(self._check_python_version())
        report.results.append(self._check_package("agent_contracts", fatal=False))
        report.results.append(self._check_package("agent_tools", fatal=False))
        report.results.append(self._check_package("harness", fatal=True))
        report.results.append(self._check_tool_packs())
        report.results.append(self._check_genesis_agents())
        report.results.append(self._check_shared_agents())
        report.results.append(self._check_workspace_yaml())
        report.results.append(self._check_sibling_repo("agent-contracts"))
        report.results.append(self._check_sibling_repo("agent-tools"))
        report.results.append(self._check_acme_backend())

        return report

    # ── Individual checks ──────────────────────────────────────────────

    def _check_python_version(self) -> CheckResult:
        """Python >= 3.11 required."""
        major, minor = sys.version_info[:2]
        if (major, minor) >= (3, 11):
            return CheckResult("python_version", CheckLevel.PASS, f"Python {major}.{minor}")
        return CheckResult(
            "python_version", CheckLevel.FATAL,
            f"Python {major}.{minor} — requires >= 3.11",
        )

    def _check_package(self, package_name: str, fatal: bool = False) -> CheckResult:
        """Check if a Python package is importable."""
        try:
            mod = importlib.import_module(package_name)
            location = getattr(mod, "__file__", "unknown")
            return CheckResult(
                f"package_{package_name}", CheckLevel.PASS,
                f"{package_name} installed",
                detail=f"at {location}",
            )
        except ImportError as exc:
            level = CheckLevel.FATAL if fatal else CheckLevel.SKIP
            return CheckResult(
                f"package_{package_name}", level,
                f"{package_name} not importable: {exc}",
            )

    def _check_tool_packs(self) -> CheckResult:
        """Check if agent-tools packs directory exists."""
        try:
            import agent_tools
            packs_dir = Path(str(agent_tools.__file__)).parent / "packs"
            if packs_dir.is_dir():
                pack_count = sum(1 for p in packs_dir.iterdir() if p.is_dir())
                return CheckResult(
                    "tool_packs", CheckLevel.PASS,
                    f"Tool packs directory found ({pack_count} packs)",
                    detail=str(packs_dir),
                )
            return CheckResult(
                "tool_packs", CheckLevel.WARN,
                "Tool packs directory not found (toolpacks optional)",
                detail=str(packs_dir),
            )
        except ImportError:
            return CheckResult(
                "tool_packs", CheckLevel.WARN,
                "agent_tools not installed — tool packs unavailable",
            )

    def _check_genesis_agents(self) -> CheckResult:
        """Check genesis agents exist in PROJECT_ROOT/agents/_genesis/."""
        genesis_dir = self.project_root / "agents" / "_genesis"
        if not genesis_dir.is_dir():
            return CheckResult(
                "genesis_agents", CheckLevel.FATAL,
                f"Genesis agents directory missing: {genesis_dir}",
            )
        agent_dirs = [d for d in genesis_dir.iterdir() if d.is_dir() and not d.name.startswith(".")]
        if len(agent_dirs) >= 1:
            return CheckResult(
                "genesis_agents", CheckLevel.PASS,
                f"Genesis agents found ({len(agent_dirs)} agents)",
                detail=str(genesis_dir),
            )
        return CheckResult(
            "genesis_agents", CheckLevel.FATAL,
            f"No genesis agents in {genesis_dir}",
        )

    def _check_shared_agents(self) -> CheckResult:
        """Check shared agents exist in PROJECT_ROOT/agents/_shared/."""
        shared_dir = self.project_root / "agents" / "_shared"
        if not shared_dir.is_dir():
            return CheckResult(
                "shared_agents", CheckLevel.FATAL,
                f"Shared agents directory missing: {shared_dir}",
            )
        agent_dirs = [d for d in shared_dir.iterdir() if d.is_dir() and not d.name.startswith(".")]
        if len(agent_dirs) >= 1:
            return CheckResult(
                "shared_agents", CheckLevel.PASS,
                f"Shared agents found ({len(agent_dirs)} agents)",
                detail=str(shared_dir),
            )
        return CheckResult(
            "shared_agents", CheckLevel.FATAL,
            f"No shared agents in {shared_dir}",
        )

    def _check_workspace_yaml(self) -> CheckResult:
        """Check workspace.yaml is loadable."""
        ws_path = self.project_root / "config" / "workspace.yaml"
        if not ws_path.exists():
            return CheckResult(
                "workspace_yaml", CheckLevel.WARN,
                "workspace.yaml not found",
                detail=str(ws_path),
            )
        try:
            import yaml
            data = yaml.safe_load(ws_path.read_text(encoding="utf-8"))
            if data and isinstance(data, dict):
                return CheckResult(
                    "workspace_yaml", CheckLevel.PASS,
                    "workspace.yaml valid",
                    detail=str(ws_path),
                )
            return CheckResult(
                "workspace_yaml", CheckLevel.WARN,
                "workspace.yaml is empty or not a mapping",
            )
        except Exception as exc:
            return CheckResult(
                "workspace_yaml", CheckLevel.WARN,
                f"workspace.yaml parse error: {exc}",
            )

    def _check_sibling_repo(self, repo_name: str) -> CheckResult:
        """Check if a sibling repo directory exists (info only)."""
        sibling = self.project_root.parent / repo_name
        if sibling.is_dir():
            return CheckResult(
                f"sibling_{repo_name}", CheckLevel.INFO,
                f"Sibling repo found: ../{repo_name}",
                detail=str(sibling),
            )
        return CheckResult(
            f"sibling_{repo_name}", CheckLevel.INFO,
            f"Sibling repo not found: ../{repo_name} (optional for smoke tests)",
        )

    def _check_acme_backend(self) -> CheckResult:
        """Check if acme-backend domain is reachable."""
        acme_dir = self.project_root.parent / "acme-backend"
        domain_yaml = acme_dir / "domain.yaml"
        if domain_yaml.exists():
            return CheckResult(
                "acme_backend", CheckLevel.PASS,
                "acme-backend domain reachable",
                detail=str(acme_dir),
            )
        if acme_dir.is_dir():
            return CheckResult(
                "acme_backend", CheckLevel.WARN,
                "acme-backend directory exists but no domain.yaml",
                detail=str(acme_dir),
            )
        return CheckResult(
            "acme_backend", CheckLevel.SKIP,
            "acme-backend not found — acme-specific tests will be skipped",
        )

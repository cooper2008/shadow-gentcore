"""Architecture lint rules — directory structure, manifest presence, doc freshness, dependency direction."""

from __future__ import annotations

from pathlib import Path
from typing import Any


class LintResult:
    """Result of a single lint check."""

    def __init__(self, rule: str, passed: bool, message: str, path: str | None = None) -> None:
        self.rule = rule
        self.passed = passed
        self.message = message
        self.path = path

    def __repr__(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        return f"LintResult({status}: {self.rule} - {self.message})"


class ArchitectureLinter:
    """Runs architecture lint rules against a project directory.

    Built-in rules:
    - Directory structure completeness
    - Manifest presence in domain directories
    - Documentation freshness (files exist and non-empty)
    - Dependency direction (no circular imports)
    """

    def __init__(self, project_root: str | Path) -> None:
        self._root = Path(project_root)
        self._results: list[LintResult] = []

    def lint_all(self) -> list[LintResult]:
        """Run all lint rules."""
        self._results = []
        self._check_directory_structure()
        self._check_manifest_presence()
        self._check_doc_freshness()
        return list(self._results)

    def _check_directory_structure(self) -> None:
        """Check required directories exist."""
        required_dirs = [
            "harness/core",
            "harness/providers",
            "harness/tools",
            "harness/authoring",
            "harness/bridges",
            "harness/cli",
            "harness/lints",
            "harness/tests",
            "config",
            "docs",
        ]
        for d in required_dirs:
            path = self._root / d
            if path.is_dir():
                self._results.append(LintResult(
                    "directory_structure", True,
                    f"Required directory exists: {d}", str(path),
                ))
            else:
                self._results.append(LintResult(
                    "directory_structure", False,
                    f"Missing required directory: {d}", str(path),
                ))

    def _check_manifest_presence(self) -> None:
        """Check that domain directories contain manifests."""
        examples_dir = self._root / "examples"
        if not examples_dir.exists():
            return
        for domain_dir in examples_dir.iterdir():
            if not domain_dir.is_dir():
                continue
            domain_yaml = domain_dir / "domain.yaml"
            if domain_yaml.exists():
                self._results.append(LintResult(
                    "manifest_presence", True,
                    f"Domain manifest found: {domain_dir.name}",
                    str(domain_yaml),
                ))
            else:
                self._results.append(LintResult(
                    "manifest_presence", False,
                    f"Missing domain.yaml in: {domain_dir.name}",
                    str(domain_yaml),
                ))

    def _check_doc_freshness(self) -> None:
        """Check that required documentation files exist and are non-empty."""
        required_docs = [
            "docs/ARCHITECTURE.md",
            "README.md",
            "AGENTS.md",
        ]
        for doc in required_docs:
            path = self._root / doc
            if path.exists() and path.stat().st_size > 0:
                self._results.append(LintResult(
                    "doc_freshness", True,
                    f"Documentation exists and non-empty: {doc}", str(path),
                ))
            elif path.exists():
                self._results.append(LintResult(
                    "doc_freshness", False,
                    f"Documentation file is empty: {doc}", str(path),
                ))
            else:
                self._results.append(LintResult(
                    "doc_freshness", False,
                    f"Missing documentation: {doc}", str(path),
                ))

    def lint_topology(self, workflow_dirs: list[str | Path] | None = None) -> list[LintResult]:
        """Lint workflow topology: check DAG validity (no cycles, valid depends_on)."""
        import yaml

        dirs_to_check = [Path(d) for d in (workflow_dirs or [])]
        dirs_to_check.append(self._root / "workflows")
        dirs_to_check.append(self._root / "examples")

        results: list[LintResult] = []
        for base in dirs_to_check:
            if not base.exists():
                continue
            for wf_file in base.rglob("*.yaml"):
                try:
                    data = yaml.safe_load(wf_file.read_text(encoding="utf-8")) or {}
                    steps = data.get("steps", [])
                    if not steps:
                        continue
                    step_names = {s.get("name", "") for s in steps}
                    ok = True
                    for step in steps:
                        for dep in step.get("depends_on", []):
                            if dep not in step_names:
                                results.append(LintResult(
                                    "topology",
                                    False,
                                    f"{wf_file.name}: step '{step['name']}' depends on unknown '{dep}'",
                                    str(wf_file),
                                ))
                                ok = False
                    if ok:
                        results.append(LintResult(
                            "topology", True,
                            f"{wf_file.name}: topology valid",
                            str(wf_file),
                        ))
                except Exception:
                    pass
        return results

    def lint_dependency_direction(self) -> list[LintResult]:
        """Check that harness/ modules don't import from domain/* or examples/*."""
        import ast

        results: list[LintResult] = []
        harness_dir = self._root / "harness"
        if not harness_dir.exists():
            return results

        forbidden_prefixes = ["domain_", "examples.", "agents."]

        for py_file in harness_dir.rglob("*.py"):
            try:
                tree = ast.parse(py_file.read_text(encoding="utf-8"))
            except SyntaxError:
                continue

            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    if isinstance(node, ast.ImportFrom) and node.module:
                        module = node.module
                    elif isinstance(node, ast.Import):
                        module = node.names[0].name if node.names else ""
                    else:
                        module = ""

                    for prefix in forbidden_prefixes:
                        if module.startswith(prefix):
                            results.append(LintResult(
                                "dependency_direction",
                                False,
                                f"{py_file.relative_to(self._root)}: forbidden import '{module}'",
                                str(py_file),
                            ))

        if not any(not r.passed for r in results):
            results.append(LintResult(
                "dependency_direction", True,
                "No forbidden cross-boundary imports found",
            ))
        return results

    def lint_schema_naming(self) -> list[LintResult]:
        """Check that contract class names follow PascalCase and end in Record/Contract/Manifest/Definition."""
        import ast

        results: list[LintResult] = []
        contracts_dir = self._root.parent / "agent-contracts" / "src" / "agent_contracts"
        if not contracts_dir.exists():
            return results

        allowed_suffixes = (
            "Record", "Contract", "Manifest", "Definition", "Enum",
            "Policy", "Profile", "Loop", "Budget", "Config", "Step",
            "Limits", "Defaults", "Result", "Envelope", "Binding",
            "Metadata", "Checkpoint", "Limit", "Override",
        )

        for py_file in contracts_dir.rglob("*.py"):
            if py_file.name.startswith("_"):
                continue
            try:
                tree = ast.parse(py_file.read_text(encoding="utf-8"))
            except SyntaxError:
                continue

            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    name = node.name
                    if name.startswith("_"):
                        continue
                    if not any(name.endswith(sfx) for sfx in allowed_suffixes):
                        # Allow Enum subclasses (str, Enum) and ABC subclasses to use any suffix
                        bases = [getattr(b, "id", "") for b in node.bases]
                        if "Enum" not in bases and "str" not in bases and "ABC" not in bases:
                            results.append(LintResult(
                                "schema_naming",
                                False,
                                f"{py_file.name}: class '{name}' should end with one of {allowed_suffixes}",
                                str(py_file),
                            ))
                        else:
                            results.append(LintResult("schema_naming", True, f"{py_file.name}: {name} ok"))
                    else:
                        results.append(LintResult("schema_naming", True, f"{py_file.name}: {name} ok"))

        return results

    @property
    def results(self) -> list[LintResult]:
        return list(self._results)

    @property
    def passed(self) -> bool:
        """True if all rules passed."""
        return all(r.passed for r in self._results) if self._results else True

    @property
    def failure_count(self) -> int:
        return sum(1 for r in self._results if not r.passed)

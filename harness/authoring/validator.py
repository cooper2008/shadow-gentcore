"""Validator — validates manifests, schemas, topology, and enforces workflow policy baseline."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

POLICY_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "workflow_policy.yaml"


class ValidationResult:
    """Result of a validation run."""

    def __init__(self) -> None:
        self.errors: list[dict[str, Any]] = []
        self.warnings: list[dict[str, Any]] = []

    def add_error(self, rule: str, message: str, path: str = "") -> None:
        self.errors.append({"rule": rule, "message": message, "path": path})

    def add_warning(self, rule: str, message: str, path: str = "") -> None:
        self.warnings.append({"rule": rule, "message": message, "path": path})

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0

    @property
    def summary(self) -> str:
        status = "PASS" if self.is_valid else "FAIL"
        return f"{status}: {len(self.errors)} errors, {len(self.warnings)} warnings"


def _load_policy() -> dict[str, Any]:
    """Load workflow policy from config/workflow_policy.yaml."""
    if POLICY_PATH.exists():
        return yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8")) or {}
    return {}


def classify_agent(manifest: dict[str, Any]) -> set[str]:
    """Classify an agent into ALL matching workflow capabilities.

    Returns a set of capabilities like {'build', 'verify', 'execute', 'review', 'plan', 'scan', 'report'}.
    An agent can match multiple capabilities.
    """
    perms = manifest.get("permissions", {})
    mode = manifest.get("execution_mode", {})
    primary = mode.get("primary", "") if isinstance(mode, dict) else str(mode)
    caps: set[str] = set()

    can_write = perms.get("file_edit") == "allow" or perms.get("file_create") == "allow"
    can_shell = perms.get("shell_command") == "allow"
    is_readonly = perms.get("file_edit") in ("deny", None) and perms.get("shell_command") in ("deny", None)

    # build = writes/modifies files
    if can_write:
        caps.add("build")
        caps.add("report")  # anything that writes can also report

    # execute = runs shell commands (deploy, provision, test)
    if can_shell:
        caps.add("execute")
        caps.add("verify")
        caps.add("validate")
        caps.add("scan")

    # review/analyze = reasoning mode + read-only
    if is_readonly and primary == "chain_of_thought":
        caps.add("review")
        caps.add("analyze")

    # plan/scan = any read-only agent
    if is_readonly:
        caps.add("plan")
        caps.add("scan")
        caps.add("analyze")

    # If nothing matched, at least it can analyze
    if not caps:
        caps.add("analyze")

    return caps


class Validator:
    """Validates domain manifests, agent manifests, workflow definitions,
    and enforces the platform workflow policy baseline.
    """

    def __init__(self) -> None:
        self._policy = _load_policy()

    def validate_domain(self, domain_path: Path) -> ValidationResult:
        """Validate a domain directory structure, manifests, and policy compliance."""
        result = ValidationResult()

        manifest_path = domain_path / "domain.yaml"
        if not manifest_path.exists():
            result.add_error("domain_manifest", "domain.yaml not found", str(domain_path))
            return result

        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = yaml.safe_load(f)

        for field in ("name", "owner", "purpose"):
            if field not in manifest:
                result.add_error("required_field", f"Missing required field: {field}", str(manifest_path))

        if not (domain_path / "agents").is_dir():
            result.add_warning("directory", "agents/ directory missing", str(domain_path))

        if not (domain_path / "workflows").is_dir():
            result.add_warning("directory", "workflows/ directory missing", str(domain_path))

        # Validate context requirements
        self._validate_context(domain_path, result)

        # Validate agent manifests within domain
        agents_dir = domain_path / "agents"
        if agents_dir.is_dir():
            for agent_manifest in agents_dir.rglob("agent_manifest.yaml"):
                agent_result = self.validate_agent_manifest(agent_manifest)
                result.errors.extend(agent_result.errors)
                result.warnings.extend(agent_result.warnings)

        # Validate workflow definitions + policy compliance
        workflows_dir = domain_path / "workflows"
        if workflows_dir.is_dir():
            for wf_file in workflows_dir.glob("*.yaml"):
                wf_result = self.validate_workflow(wf_file, domain_path)
                result.errors.extend(wf_result.errors)
                result.warnings.extend(wf_result.warnings)

        logger.info("Domain validation %s: %s", domain_path.name, result.summary)
        return result

    def validate_agent_manifest(self, manifest_path: Path) -> ValidationResult:
        """Validate a single agent manifest against required fields."""
        result = ValidationResult()

        if not manifest_path.exists():
            result.add_error("file_missing", "Agent manifest not found", str(manifest_path))
            return result

        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = yaml.safe_load(f)

        # Required fields
        for field in ("id", "domain", "execution_mode"):
            if field not in manifest:
                result.add_error("required_field", f"Missing: {field}", str(manifest_path))

        # Policy-required fields
        for field in self._policy.get("required_agent_fields", []):
            if field not in manifest:
                result.add_warning(
                    "policy_field",
                    f"Policy requires field '{field}' in agent manifest",
                    str(manifest_path),
                )

        # Check ID format: domain/AgentName/version
        agent_id = manifest.get("id", "")
        if agent_id and agent_id.count("/") != 2:
            result.add_warning("id_format", f"Agent ID should be domain/Name/version: {agent_id}", str(manifest_path))

        # Check system prompt ref exists and is non-trivial
        prompt_ref = manifest.get("system_prompt_ref")
        if prompt_ref:
            prompt_path = manifest_path.parent / prompt_ref
            if not prompt_path.exists():
                result.add_error("system_prompt", f"System prompt not found: {prompt_ref}", str(manifest_path))
            else:
                min_len = self._policy.get("quality", {}).get("min_system_prompt_length", 50)
                content = prompt_path.read_text(encoding="utf-8")
                if len(content) < min_len:
                    result.add_warning(
                        "prompt_quality",
                        f"System prompt too short ({len(content)} chars < {min_len} min)",
                        str(prompt_path),
                    )

        # Validate permissions
        permissions = manifest.get("permissions", {})
        valid_permissions = {"file_edit", "file_create", "shell_command", "network_access", "browser_access"}
        for perm in permissions:
            if perm not in valid_permissions:
                result.add_warning("permission", f"Unknown permission: {perm}", str(manifest_path))

        return result

    def validate_workflow(self, workflow_path: Path, domain_path: Path | None = None) -> ValidationResult:
        """Validate a workflow definition — topology + policy baseline."""
        result = ValidationResult()

        if not workflow_path.exists():
            result.add_error("file_missing", "Workflow file not found", str(workflow_path))
            return result

        with open(workflow_path, "r", encoding="utf-8") as f:
            wf = yaml.safe_load(f)

        for field in ("name", "steps"):
            if field not in wf:
                result.add_error("required_field", f"Missing: {field}", str(workflow_path))

        steps = wf.get("steps", [])
        step_names = {s.get("name") for s in steps}

        # Validate topology: depends_on references exist
        for step in steps:
            for dep in step.get("depends_on", []):
                if dep not in step_names:
                    result.add_error(
                        "topology",
                        f"Step '{step.get('name')}' depends on unknown step '{dep}'",
                        str(workflow_path),
                    )

        # Self-dependency check
        for step in steps:
            name = step.get("name", "")
            if name in set(step.get("depends_on", [])):
                result.add_error("topology", f"Step '{name}' depends on itself", str(workflow_path))

        # Budget requirement
        if "budget" in self._policy.get("required_workflow_fields", []):
            if "budget" not in wf:
                result.add_warning("policy_budget", "Workflow missing 'budget' section (policy requires it)", str(workflow_path))

        # ── POLICY BASELINE CHECK ──
        self._validate_workflow_policy(wf, steps, domain_path, workflow_path, result)

        return result

    def _validate_workflow_policy(
        self,
        wf: dict[str, Any],
        steps: list[dict[str, Any]],
        domain_path: Path | None,
        workflow_path: Path,
        result: ValidationResult,
    ) -> None:
        """Check workflow using universal capability-pair rules.

        Instead of fixed profiles, uses rules like:
          "If workflow has 'build', it should also have 'verify'"
        This works for ANY domain type — dev, ops, security, incident, docs, etc.
        """
        # Minimum step count
        min_steps = self._policy.get("min_workflow_steps", 2)
        if len(steps) < min_steps:
            result.add_error(
                "policy_baseline",
                f"Workflow has {len(steps)} step(s), minimum is {min_steps}",
                str(workflow_path),
            )

        # Classify each step — collect ALL capabilities
        all_found_caps: set[str] = set()
        step_caps: dict[str, set[str]] = {}
        project_root = Path(__file__).resolve().parent.parent.parent

        for step in steps:
            agent_id = step.get("agent", "")
            step_name = step.get("name", "")

            agent_manifest = self._find_agent_manifest(agent_id, domain_path, project_root)
            if agent_manifest:
                caps = classify_agent(agent_manifest)
            else:
                caps = self._classify_by_name(step_name, agent_id)
            step_caps[step_name] = caps
            all_found_caps.update(caps)

        # Apply capability-pair rules
        for rule in self._policy.get("capability_rules", []):
            if_cap = rule.get("if", "")
            then_cap = rule.get("then", "")
            level = rule.get("level", "warning")
            message = rule.get("message", f"Workflow has '{if_cap}' but missing '{then_cap}'")

            if if_cap in all_found_caps and then_cap not in all_found_caps:
                if level == "error":
                    result.add_error("policy_baseline", message, str(workflow_path))
                else:
                    result.add_warning("policy_baseline", message, str(workflow_path))

        # Gate policy
        gate_policy = self._policy.get("gate_policy", {})
        for step in steps:
            step_name = step.get("name", "")
            caps = step_caps.get(step_name, set())
            has_gate = "gate" in step

            if "verify" in caps and gate_policy.get("verify_steps_must_have_gate") and not has_gate:
                result.add_warning("policy_gate", f"Step '{step_name}' runs checks but has no gate", str(workflow_path))
            if "execute" in caps and gate_policy.get("execute_steps_must_have_gate") and not has_gate:
                result.add_warning("policy_gate", f"Step '{step_name}' executes commands but has no gate", str(workflow_path))

    def _find_agent_manifest(
        self, agent_id: str, domain_path: Path | None, project_root: Path
    ) -> dict[str, Any] | None:
        """Try to locate and load an agent manifest from agent_id."""
        parts = agent_id.split("/")
        if len(parts) < 3:
            return None

        candidates = []
        if domain_path:
            candidates.append(domain_path / "agents" / parts[1] / parts[2] / "agent_manifest.yaml")
        candidates.append(project_root / "agents" / parts[0] / parts[1] / parts[2] / "agent_manifest.yaml")

        for candidate in candidates:
            if candidate.exists():
                return yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
        return None

    @staticmethod
    def _classify_by_name(step_name: str, agent_id: str) -> set[str]:
        """Fallback classification when manifest can't be loaded."""
        combined = f"{step_name} {agent_id}".lower()
        caps: set[str] = set()
        if any(w in combined for w in ("code", "build", "gen", "write", "create")):
            caps.update({"build", "report"})
        if any(w in combined for w in ("test", "lint", "check", "verify", "health")):
            caps.update({"verify", "validate", "scan"})
        if any(w in combined for w in ("review", "audit", "evaluate", "analyze", "diagnose")):
            caps.update({"review", "analyze"})
        if any(w in combined for w in ("deploy", "provision", "apply", "execute", "run")):
            caps.update({"execute"})
        if any(w in combined for w in ("plan", "spec", "design")):
            caps.update({"plan", "scan"})
        if any(w in combined for w in ("report", "notify", "document", "postmortem")):
            caps.update({"report"})
        if any(w in combined for w in ("scan", "detect", "monitor", "read")):
            caps.update({"scan"})
        return caps or {"analyze"}

    def _validate_context(self, domain_path: Path, result: ValidationResult) -> None:
        """Check required context files exist and are non-empty."""
        for ctx_entry in self._policy.get("required_context", []):
            # Support both string format and dict format
            if isinstance(ctx_entry, str):
                ctx_ref = ctx_entry
                min_len = 100
                level = "warning"
            else:
                ctx_ref = ctx_entry.get("path", "")
                min_len = ctx_entry.get("min_length", 100)
                level = ctx_entry.get("level", "warning")

            if not ctx_ref:
                continue

            ctx_path = domain_path / ctx_ref
            if not ctx_path.exists():
                if level == "error":
                    result.add_error("policy_context", f"Required file missing: '{ctx_ref}'", str(domain_path))
                else:
                    result.add_warning("policy_context", f"Recommended file missing: '{ctx_ref}'", str(domain_path))
            elif len(ctx_path.read_text(encoding="utf-8")) < min_len:
                result.add_warning(
                    "policy_context",
                    f"'{ctx_ref}' exists but is too short (<{min_len} chars)",
                    str(ctx_path),
                )

    def validate_port_refs(self, domain_path: Path) -> ValidationResult:
        """Check that port references in workflows match domain ports."""
        result = ValidationResult()

        manifest_path = domain_path / "domain.yaml"
        if not manifest_path.exists():
            return result

        with open(manifest_path, "r", encoding="utf-8") as f:
            domain = yaml.safe_load(f)

        domain_ports = {p["name"] for p in domain.get("ports", [])}

        workflows_dir = domain_path / "workflows"
        if workflows_dir.is_dir():
            for wf_file in workflows_dir.glob("*.yaml"):
                with open(wf_file, "r", encoding="utf-8") as f:
                    wf = yaml.safe_load(f)
                for port in wf.get("ports", []):
                    port_name = port.get("name", "")
                    if port_name and port_name not in domain_ports:
                        result.add_warning(
                            "port_ref",
                            f"Workflow port '{port_name}' not in domain ports",
                            str(wf_file),
                        )

        return result

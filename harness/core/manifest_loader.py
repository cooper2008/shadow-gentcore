"""ManifestLoader — boots the agent framework from YAML manifests.

Responsibilities:
- Load domain.yaml, agent_manifest.yaml, workflow YAML files
- Resolve system_prompt_ref to file content
- Load domain context_files (standards, architecture docs)
- Resolve tool pack declarations → register built-in adapters in ToolExecutor
- Build step_configs dict ready for CompositionEngine.execute_dag()
- Wire AgentRunner + CompositionEngine into a runnable engine
"""

from __future__ import annotations

import logging
import warnings
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


class ManifestLoader:
    """Loads YAML manifests and wires the framework into a runnable engine."""

    def __init__(self, packs_root: str | Path | None = None) -> None:
        # Optional override for tool pack YAML directory
        self._packs_root = Path(packs_root) if packs_root else None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_yaml(self, path: str | Path) -> dict[str, Any]:
        """Load any YAML file into a plain dict."""
        p = Path(path)
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}

    def load_domain(self, domain_path: str | Path) -> dict[str, Any]:
        """Load domain.yaml from a domain directory. Returns empty dict if not found."""
        p = Path(domain_path)
        yaml_file = p / "domain.yaml" if p.is_dir() else p
        if not yaml_file.exists():
            return {}
        data = self.load_yaml(yaml_file)
        try:
            from agent_contracts.manifests.domain_manifest import DomainManifest
            DomainManifest.model_validate(data)
        except Exception as exc:
            warnings.warn(f"Domain manifest validation warning for {yaml_file}: {exc}", stacklevel=2)
        return data

    def load_agent(
        self,
        agent_path: str | Path,
        domain_root: str | Path | None = None,
        domain_manifest: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], str, list[dict[str, Any]]]:
        """Load an agent directory and return (manifest, system_prompt, context_items).

        Args:
            agent_path: Path to the agent version directory (e.g., agents/CodeGenAgent/v1/)
            domain_root: Root directory of the domain (for resolving context_files)
            domain_manifest: Already-loaded domain manifest (avoids re-reading)

        Returns:
            (manifest dict, system_prompt content, context_items list)
        """
        agent_dir = Path(agent_path)
        manifest = self.load_yaml(agent_dir / "agent_manifest.yaml")
        try:
            from agent_contracts.manifests.agent_manifest import AgentManifest
            AgentManifest.model_validate(manifest)
        except Exception as exc:
            warnings.warn(f"Agent manifest validation warning for {agent_dir}: {exc}", stacklevel=2)

        # Resolve system_prompt_ref
        system_prompt = ""
        prompt_ref = manifest.get("system_prompt_ref", "system_prompt.md")
        prompt_path = agent_dir / prompt_ref
        if prompt_path.exists():
            system_prompt = prompt_path.read_text(encoding="utf-8")

        # Load domain context files (standards, architecture docs)
        context_items: list[dict[str, Any]] = []
        if domain_root and domain_manifest:
            context_items = self._load_context_files(
                Path(domain_root), domain_manifest.get("context_files", [])
            )

        # Auto-inject standards/glossary when agent manifest has context.inject_standards: true
        agent_ctx = manifest.get("context", {})
        if isinstance(agent_ctx, dict) and agent_ctx.get("inject_standards") and domain_root:
            dr = Path(domain_root)
            for ctx_key, default_path in [
                ("standards_path", "context/standards.md"),
                ("glossary_path", "context/glossary.md"),
            ]:
                ctx_path = dr / agent_ctx.get(ctx_key, default_path)
                if ctx_path.exists():
                    context_items.append({
                        "source": f"domain_context:{ctx_path.name}",
                        "content": ctx_path.read_text(encoding="utf-8"),
                        "priority": 10,
                    })

        # Load optional Python hooks referenced by hooks_ref field
        hooks: dict[str, Any] = {}
        hooks_ref = manifest.get("hooks_ref")
        if hooks_ref:
            hooks_path = agent_dir / hooks_ref
            if hooks_path.exists():
                import importlib.util
                spec = importlib.util.spec_from_file_location("agent_hooks", hooks_path)
                mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
                spec.loader.exec_module(mod)  # type: ignore[union-attr]
                for hook_name in ("pre_execute", "post_execute", "pre_tool_call"):
                    if hasattr(mod, hook_name):
                        hooks[hook_name] = getattr(mod, hook_name)
            else:
                logger.warning(
                    "hooks_ref '%s' specified in agent manifest at '%s' but file not found — hooks skipped",
                    hooks_ref,
                    agent_dir,
                )
        manifest["_hooks"] = hooks

        return manifest, system_prompt, context_items

    def load_workflow(self, workflow_path: str | Path) -> dict[str, Any]:
        """Load a workflow YAML file."""
        data = self.load_yaml(workflow_path)
        try:
            from agent_contracts.manifests.workflow_def import WorkflowDefinition
            WorkflowDefinition.model_validate(data)
        except Exception as exc:
            warnings.warn(f"Workflow manifest validation warning for {workflow_path}: {exc}", stacklevel=2)
        return data

    def build_step_configs(
        self,
        workflow: dict[str, Any],
        domain_root: str | Path,
        domain_manifest: dict[str, Any],
        task_input: dict[str, Any] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Build step_configs dict for CompositionEngine from workflow + domain.

        Args:
            workflow: Loaded workflow dict
            domain_root: Root directory of the domain
            domain_manifest: Loaded domain manifest
            task_input: Optional initial task input injected into first step

        Returns:
            step_configs dict: {step_name: {manifest, task, system_prompt, context_items}}
        """
        domain_root = Path(domain_root)
        step_configs: dict[str, dict[str, Any]] = {}

        steps = workflow.get("steps", [])

        for step in steps:
            # Support both "name" and "id" keys (domain agents use "id")
            step_name = step.get("name") or step.get("id", "")
            agent_id = step.get("agent", "")

            # Locate agent directory from agent_id (e.g. "backend_fastapi/FastAPICodeGenAgent/v1")
            parts = agent_id.split("/")
            agent_dir = domain_root / "agents" / agent_id  # fallback
            project_root = Path(__file__).resolve().parent.parent.parent
            if len(parts) >= 3:
                agent_name = parts[1]
                agent_version = parts[2]
                # Try: domain_root/agents/<AgentName>/<version>/ (normal domain)
                candidate = domain_root / "agents" / agent_name / agent_version
                if candidate.exists():
                    agent_dir = candidate
                else:
                    # Try: domain_root/agents/<domain>/<AgentName>/<version>/ (internal domains)
                    candidate2 = domain_root / "agents" / parts[0] / agent_name / agent_version
                    if candidate2.exists():
                        agent_dir = candidate2
                    else:
                        # Try: project_root/agents/<domain>/<AgentName>/<version>/ (shared/internal agents)
                        candidate3 = project_root / "agents" / parts[0] / agent_name / agent_version
                        if candidate3.exists():
                            agent_dir = candidate3
            elif len(parts) == 2:
                agent_dir = domain_root / "agents" / parts[0] / parts[1]

            # Load agent if directory exists
            manifest: dict[str, Any] = {}
            system_prompt = ""
            context_items: list[dict[str, Any]] = []

            if agent_dir.exists():
                manifest, system_prompt, context_items = self.load_agent(
                    agent_dir, domain_root, domain_manifest
                )
            else:
                # Fallback: just use agent_id as identifier
                manifest = {"id": agent_id}

            # Build task dict — task_input is available to ALL steps (not just first)
            # so every agent can access source paths, industry, domain_name, etc.
            task: dict[str, Any] = {"agent_id": agent_id, "step": step_name}
            if task_input:
                task.update(task_input)

            step_configs[step_name] = {
                "manifest": manifest,
                "task": task,
                "system_prompt": system_prompt,
                "context_items": context_items,
            }

        return step_configs

    def boot_engine(
        self,
        workflow_path: str | Path,
        domain_root: str | Path | None = None,
        provider: Any = None,
        task_input: dict[str, Any] | None = None,
    ) -> tuple[Any, dict[str, Any], dict[str, dict[str, Any]]]:
        """Boot a CompositionEngine from a workflow YAML file.

        Args:
            workflow_path: Path to workflow YAML
            domain_root: Domain root dir. Defaults to workflow's parent's parent.
            provider: LLM provider instance (optional, for real execution)
            task_input: Initial task input for first workflow step

        Returns:
            (engine, workflow_dict, step_configs) ready to call engine.execute_dag(steps, step_configs)
        """
        from harness.core.agent_runner import AgentRunner
        from harness.core.composition_engine import CompositionEngine
        from harness.core.tool_executor import ToolExecutor
        from harness.tools.builtin import register_builtins

        workflow_path = Path(workflow_path)
        workflow = self.load_workflow(workflow_path)

        # Infer domain root: workflow is at <domain_root>/workflows/<name>.yaml
        if domain_root is None:
            domain_root = workflow_path.parent.parent

        domain_root = Path(domain_root)
        domain_manifest = self.load_domain(domain_root)

        # Build rule engine (hot-reloadable from config/rules.yaml)
        from harness.core.rule_engine import RuleEngine
        rule_engine = RuleEngine()

        # Build tool executor with rule enforcement + built-in adapters + MCP servers
        tool_executor = ToolExecutor(rule_engine=rule_engine)
        register_builtins(tool_executor)

        # Resolve toolpack:// URIs from agent manifests and register HTTP adapters
        try:
            import agent_tools as _at
            _packs_root = self._packs_root or (Path(str(_at.__file__)).parent / "packs")
            resolver = _at.ToolResolver(pack_dirs=[_packs_root])
            # Collect all toolpack:// and tool:// URIs across workflow steps
            pack_uris: list[str] = []
            for step in workflow.get("steps", []):
                agent_id = step.get("agent", "")
                parts = agent_id.split("/")
                candidate_dir = None
                if len(parts) >= 2:
                    project_root = Path(__file__).resolve().parent.parent.parent
                    candidate_dir = project_root / "agents" / parts[0] / "/".join(parts[1:])
                if candidate_dir and (candidate_dir / "agent_manifest.yaml").exists():
                    amf = self.load_yaml(candidate_dir / "agent_manifest.yaml")
                    for tool in amf.get("tools", []):
                        pack_ref = (tool if isinstance(tool, str) else tool.get("pack", "")) or ""
                        if pack_ref.startswith("toolpack://") or pack_ref.startswith("tool://"):
                            pack_uris.append(pack_ref)
            # Register HTTP adapters for resolved packs
            for uri in set(pack_uris):
                pack = resolver.resolve_pack(uri) if uri.startswith("toolpack://") else None
                if pack is None:
                    continue
                for tool_uri in (pack.tools if hasattr(pack, "tools") else []):
                    tool_manifest = resolver.resolve_tool(tool_uri)
                    if tool_manifest and getattr(tool_manifest, "adapter_class", None) == "http_api":
                        adapter = _at.HTTPAPIToolAdapter(tool_manifest)
                        tool_name = tool_uri.replace("tool://", "")
                        tool_executor.register_adapter(tool_name, adapter)
        except Exception as exc:
            logger.debug("agent-tools integration unavailable, skipping toolpack registration: %s", exc)

        # Register MCP server tools from config/mcp_servers.yaml
        try:
            from harness.tools.mcp_loader import register_mcp_tools
            register_mcp_tools(tool_executor)
        except Exception as exc:
            logger.debug("MCP tools registration skipped (config missing or unavailable): %s", exc)

        # Build AgentRunner
        runner = AgentRunner(provider=provider, tool_executor=tool_executor)

        # Build output validator for post-execution quality checks
        from harness.core.output_validator import OutputValidator
        output_validator = OutputValidator()

        # Build CompositionEngine
        engine = CompositionEngine(agent_runner=runner, output_validator=output_validator)

        # Register feedback loops from workflow definition
        for loop_def in workflow.get("feedback_loops", []):
            from harness.core.feedback_loop import FeedbackLoop
            max_iter = loop_def.get("max_iterations") or loop_def.get("max_rounds", 2)
            condition_expr = loop_def.get("condition")
            if condition_expr:
                condition_fn = lambda result, _c=condition_expr: not engine._evaluate_condition(_c, result)
            else:
                condition_fn = None
            loop = FeedbackLoop(
                from_step=loop_def.get("from_step", ""),
                to_step=loop_def.get("to_step", ""),
                condition_fn=condition_fn,
                max_iterations=max_iter,
            )
            engine.register_feedback_loop(loop)

        # Build step configs
        step_configs = self.build_step_configs(
            workflow, domain_root, domain_manifest, task_input
        )

        return engine, workflow, step_configs

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_context_files(
        self, domain_root: Path, context_file_refs: list[str]
    ) -> list[dict[str, Any]]:
        """Load domain context files and return as context_items."""
        items: list[dict[str, Any]] = []
        for ref in context_file_refs:
            path = domain_root / ref
            if path.exists():
                content = path.read_text(encoding="utf-8")
                items.append({
                    "source": f"domain_context:{path.name}",
                    "content": content,
                    "priority": 10,  # High priority — standards stay in context
                })
        return items

"""Thin async wrapper around ManifestLoader + AgentRunner."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from harness.core.manifest_loader import ManifestLoader


def _make_provider(domain_path: str, dry_run: bool = False) -> Any:
    """Build a provider from the domain's provider.yaml, or dry-run if not found."""
    from harness.providers.dry_run import DryRunProvider

    provider_yaml = Path(domain_path) / "config" / "provider.yaml"
    if dry_run or not provider_yaml.exists():
        return DryRunProvider()

    import yaml as _yaml

    cfg = _yaml.safe_load(provider_yaml.read_text(encoding="utf-8")) or {}
    provider_name = cfg.get("provider", "anthropic")
    model = cfg.get("model", "claude-sonnet-4-6")
    max_tokens = int(cfg.get("max_tokens", 8192))

    if provider_name == "anthropic":
        from harness.providers.anthropic_provider import AnthropicProvider

        api_key_env = cfg.get("api_key_env", "ANTHROPIC_API_KEY")
        api_key = os.environ.get(api_key_env, "")
        return AnthropicProvider(api_key=api_key, model=model, max_tokens=max_tokens)

    if provider_name == "openai":
        from harness.providers.openai_provider import OpenAIProvider

        api_key_env = cfg.get("api_key_env", "OPENAI_API_KEY")
        api_key = os.environ.get(api_key_env, "")
        return OpenAIProvider(api_key=api_key, model=model)

    return DryRunProvider()


def _resolve_agent_dir(agent_id: str, domain_root: Path) -> Path | None:
    """Resolve the agent directory from a string ID.

    Handles three ID formats:
      - "AgentName/v1"                → domain/agents/AgentName/v1/
      - "_genesis/AgentName/v1"       → domain/agents/AgentName/v1/ OR
                                        project/agents/_genesis/AgentName/v1/
      - "category/AgentName/v1"       → same rules as 3-part
    """
    project_root = Path(__file__).resolve().parent.parent.parent
    parts = agent_id.split("/")

    if len(parts) >= 3:
        # First try domain/agents/<name>/<version> (user-owned agent)
        candidate = domain_root / "agents" / parts[1] / parts[2]
        if candidate.exists():
            return candidate
        # Then try project/agents/<category>/<name>/<version> (shared/internal)
        candidate2 = project_root / "agents" / parts[0] / parts[1] / parts[2]
        if candidate2.exists():
            return candidate2
    elif len(parts) == 2:
        candidate = domain_root / "agents" / parts[0] / parts[1]
        if candidate.exists():
            return candidate

    return None


async def run_agent(
    agent_id: str,
    task: str,
    domain_path: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run a single agent and return its result dict."""
    from agent_contracts.contracts.task_envelope import TaskEnvelope
    from harness.core.agent_runner import AgentRunner
    from harness.core.tool_executor import ToolExecutor
    from harness.tools.builtin import register_builtins

    loader = ManifestLoader()
    domain_root = Path(domain_path).resolve()
    provider = _make_provider(domain_path, dry_run=dry_run)

    agent_dir = _resolve_agent_dir(agent_id, domain_root)
    if agent_dir is None:
        return {"status": "error", "error": f"Agent not found: {agent_id}"}

    try:
        domain_manifest = loader.load_domain(domain_root)
        manifest, system_prompt, context_items = loader.load_agent(
            agent_dir, domain_root, domain_manifest
        )

        tool_executor = ToolExecutor()
        register_builtins(tool_executor)
        runner = AgentRunner(provider=provider, tool_executor=tool_executor)

        task_envelope = TaskEnvelope(
            task_id=f"api-{agent_id.replace('/', '-')}",
            agent_id=agent_id,
            input_payload={"instruction": task},
        )

        result = await runner.run(
            manifest=manifest,
            task=task_envelope,
            system_prompt_content=system_prompt,
            context_items=context_items,
        )
        return {"status": "completed", "output": result}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


async def run_workflow(
    workflow_path: str,
    task: dict[str, Any],
    domain_path: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run a workflow and return the execution result dict."""
    loader = ManifestLoader()
    domain_root = Path(domain_path).resolve()
    provider = _make_provider(domain_path, dry_run=dry_run)

    wf_path = Path(workflow_path)
    if not wf_path.is_absolute():
        wf_path = domain_root / workflow_path

    if not wf_path.exists():
        return {"status": "error", "error": f"Workflow not found: {workflow_path}"}

    try:
        engine, workflow_data, step_configs = loader.boot_engine(
            wf_path,
            domain_root=domain_root,
            provider=provider,
            task_input=task,
        )
        result = await engine.execute_dag(workflow_data["steps"], step_configs)
        return result
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def list_agents(domain_path: str) -> list[dict[str, str]]:
    """List all agent manifests available in the domain's agents/ directory."""
    import yaml as _yaml

    agents: list[dict[str, str]] = []
    agents_dir = Path(domain_path) / "agents"
    if not agents_dir.exists():
        return agents

    for manifest_path in sorted(agents_dir.rglob("agent_manifest.yaml")):
        try:
            data = _yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
            agents.append(
                {
                    "id": data.get("id", str(manifest_path.relative_to(agents_dir))),
                    "description": data.get("description", ""),
                    "category": data.get("category", ""),
                }
            )
        except Exception:
            pass

    return agents

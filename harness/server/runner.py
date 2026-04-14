"""Thin async wrapper around ManifestLoader + AgentRunner."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Any

from harness.core.manifest_loader import ManifestLoader

logger = logging.getLogger(__name__)

# Only allow alphanumeric, underscore, hyphen in each segment; 2 or 3 segments.
_AGENT_ID_PATTERN = re.compile(r'^[A-Za-z0-9_-]+(/[A-Za-z0-9_-]+){1,2}$')


def _validate_agent_id(agent_id: str) -> None:
    """Validate agent_id format to prevent path traversal.

    Raises ValueError if agent_id contains characters outside the allowed
    set (alphanumeric, underscore, hyphen) or has an unexpected number of
    path segments.  This blocks '..' traversal, spaces, and other injection
    vectors before the value reaches any filesystem operation.
    """
    if not _AGENT_ID_PATTERN.match(agent_id):
        raise ValueError(f"Invalid agent ID format: {agent_id}")

_ALLOWED_ENV_VARS = {
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "BEDROCK_API_KEY",
    "AZURE_OPENAI_API_KEY",
}


def _validate_domain_path(domain_path: str) -> Path:
    """Validate and resolve domain path, preventing traversal attacks."""
    resolved = Path(domain_path).resolve()
    allowed_roots_str = os.environ.get("ALLOWED_DOMAIN_ROOTS", "")
    if allowed_roots_str:
        allowed_roots = [
            Path(r.strip()).resolve()
            for r in allowed_roots_str.split(":")
            if r.strip()
        ]
        if not any(str(resolved).startswith(str(root)) for root in allowed_roots):
            logger.warning(
                "Rejected domain path '%s' (resolved: %s) — outside allowed roots",
                domain_path,
                resolved,
            )
            raise ValueError(
                f"Domain path '{domain_path}' is outside allowed roots"
            )
    return resolved


def _make_provider(domain_path: str, dry_run: bool = False) -> Any:
    """Build a provider from the domain's provider.yaml, or dry-run if not found."""
    from harness.providers.dry_run import DryRunProvider

    # GENTCORE_PROVIDER=dry-run overrides everything (for safe local testing)
    if os.environ.get("GENTCORE_PROVIDER", "") == "dry-run":
        return DryRunProvider()

    provider_yaml = Path(domain_path) / "config" / "provider.yaml"
    if dry_run or not provider_yaml.exists():
        return DryRunProvider()

    import yaml as _yaml

    cfg = _yaml.safe_load(provider_yaml.read_text(encoding="utf-8")) or {}
    provider_name = cfg.get("provider", "anthropic")
    model = cfg.get("model", "claude-sonnet-4-6")
    max_tokens = int(cfg.get("max_tokens", 8192))

    # Validate api_key_env against allowlist to prevent secret exfiltration
    api_key_env = cfg.get("api_key_env", "ANTHROPIC_API_KEY")
    if api_key_env not in _ALLOWED_ENV_VARS:
        raise ValueError(
            f"Disallowed api_key_env: {api_key_env}. "
            f"Allowed: {_ALLOWED_ENV_VARS}"
        )

    if provider_name == "anthropic":
        from harness.providers.anthropic_provider import AnthropicProvider

        api_key = os.environ.get(api_key_env, "")
        return AnthropicProvider(api_key=api_key, model=model, max_tokens=max_tokens)

    if provider_name == "openai":
        from harness.providers.openai_provider import OpenAIProvider

        api_key_env = cfg.get("api_key_env", "OPENAI_API_KEY")
        if api_key_env not in _ALLOWED_ENV_VARS:
            raise ValueError(
                f"Disallowed api_key_env: {api_key_env}. "
                f"Allowed: {_ALLOWED_ENV_VARS}"
            )
        api_key = os.environ.get(api_key_env, "")
        return OpenAIProvider(api_key=api_key, model=model)

    # Unknown provider: fail explicitly instead of silent fallback
    raise ValueError(f"Unknown provider: {provider_name}")


def _resolve_agent_dir(agent_id: str, domain_root: Path) -> Path | None:
    """Resolve the agent directory from a string ID.

    Handles three ID formats:
      - "AgentName/v1"                -> domain/agents/AgentName/v1/
      - "_genesis/AgentName/v1"       -> domain/agents/AgentName/v1/ OR
                                        project/agents/_genesis/AgentName/v1/
      - "category/AgentName/v1"       -> same rules as 3-part

    Validates the agent_id format first and verifies that resolved paths
    stay within expected directory boundaries.
    """
    _validate_agent_id(agent_id)

    project_root = Path(__file__).resolve().parent.parent.parent
    parts = agent_id.split("/")

    def _check_containment(candidate: Path) -> Path | None:
        """Return candidate only if it is under domain_root or project_root."""
        if not candidate.exists():
            return None
        resolved = candidate.resolve()
        domain_resolved = domain_root.resolve()
        project_resolved = project_root.resolve()
        if not (
            str(resolved).startswith(str(domain_resolved))
            or str(resolved).startswith(str(project_resolved))
        ):
            logger.warning(
                "Rejected agent path '%s' — outside allowed roots", resolved
            )
            return None
        return candidate

    if len(parts) >= 3:
        # First try domain/agents/<name>/<version> (user-owned agent)
        result = _check_containment(domain_root / "agents" / parts[1] / parts[2])
        if result is not None:
            return result
        # Then try project/agents/<category>/<name>/<version> (shared/internal)
        result = _check_containment(
            project_root / "agents" / parts[0] / parts[1] / parts[2]
        )
        if result is not None:
            return result
    elif len(parts) == 2:
        result = _check_containment(domain_root / "agents" / parts[0] / parts[1])
        if result is not None:
            return result

    return None


async def run_agent(
    agent_id: str,
    task: str | dict[str, Any],
    domain_path: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run a single agent and return its result dict."""
    from agent_contracts.contracts.task_envelope import TaskEnvelope
    from harness.core.agent_runner import AgentRunner
    from harness.core.tool_executor import ToolExecutor
    from harness.tools.builtin import register_builtins

    if isinstance(task, str):
        task_input = {"instruction": task}
    else:
        task_input = task

    try:
        domain_root = _validate_domain_path(domain_path)
    except ValueError as e:
        return {"status": "error", "error": str(e)}

    loader = ManifestLoader()
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
            task_id=f"api-{agent_id.replace('/', '-')}-{uuid.uuid4().hex[:8]}",
            agent_id=agent_id,
            input_payload=task_input,
        )

        result = await runner.run(
            manifest=manifest,
            task=task_envelope,
            system_prompt_content=system_prompt,
            context_items=context_items,
        )
        return {"status": "completed", "output": result}
    except Exception as exc:
        request_id = uuid.uuid4().hex[:8]
        logger.exception("Request %s failed", request_id)
        return {"status": "error", "error": "Internal error processing request", "request_id": request_id}


async def run_workflow(
    workflow_path: str,
    task: dict[str, Any],
    domain_path: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run a workflow and return the execution result dict."""
    try:
        domain_root = _validate_domain_path(domain_path)
    except ValueError as e:
        return {"status": "error", "error": str(e)}

    loader = ManifestLoader()
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
        request_id = uuid.uuid4().hex[:8]
        logger.exception("Request %s failed", request_id)
        return {"status": "error", "error": "Internal error processing request", "request_id": request_id}


def list_agents(domain_path: str) -> list[dict[str, str]]:
    """List all agent manifests available in the domain's agents/ directory."""
    import yaml as _yaml

    try:
        validated_root = _validate_domain_path(domain_path)
    except ValueError:
        return []

    agents: list[dict[str, str]] = []
    agents_dir = validated_root / "agents"
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
        except Exception as exc:
            logger.warning("Failed to parse agent manifest %s: %s", manifest_path, exc)

    return agents

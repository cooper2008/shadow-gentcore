"""Scaffolder — generates domain, pack, agent, and workflow scaffolds from templates."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class Scaffolder:
    """Scaffolds new domains, packs, agents, and workflows from templates.

    Uses template files from harness/templates/ to generate directory structures
    and manifest files for new components.
    """

    def __init__(self, templates_dir: Path | None = None) -> None:
        if templates_dir is None:
            templates_dir = Path(__file__).resolve().parent.parent / "templates"
        self._templates_dir = templates_dir

    def scaffold_domain(self, name: str, path: Path, owner: str = "team") -> dict[str, Any]:
        """Scaffold a new domain directory with manifest and structure.

        Creates:
        - domain.yaml
        - agents/ directory
        - workflows/ directory
        """
        domain_dir = path / name
        domain_dir.mkdir(parents=True, exist_ok=True)
        (domain_dir / "agents").mkdir(exist_ok=True)
        (domain_dir / "workflows").mkdir(exist_ok=True)

        manifest_content = (
            f"name: {name}\n"
            f"owner: {owner}\n"
            f'purpose: "{name} domain"\n'
            f'version: "0.1.0"\n'
            f"workspace_policy:\n"
            f'  root_dir: "."\n'
            f"  allowed_paths:\n"
            f'    - "src/"\n'
            f'    - "tests/"\n'
            f"  forbidden_paths:\n"
            f'    - ".env"\n'
            f"autonomy_profile: assisted\n"
            f"default_tool_packs:\n"
            f'  - "toolpack://core/filesystem"\n'
            f"metadata:\n"
            f"  team: {owner}\n"
        )
        manifest_path = domain_dir / "domain.yaml"
        manifest_path.write_text(manifest_content, encoding="utf-8")

        logger.info("Scaffolded domain '%s' at %s", name, domain_dir)
        return {
            "domain_dir": str(domain_dir),
            "files_created": [
                str(manifest_path),
            ],
            "dirs_created": [
                str(domain_dir / "agents"),
                str(domain_dir / "workflows"),
            ],
        }

    def scaffold_agent(
        self, domain_path: Path, name: str, version: str = "v1", category: str = "reasoning"
    ) -> dict[str, Any]:
        """Scaffold a new agent manifest bundle."""
        agent_dir = domain_path / "agents" / name / version
        agent_dir.mkdir(parents=True, exist_ok=True)

        domain_name = domain_path.name

        manifest_content = (
            f"id: {domain_name}/{name}/{version}\n"
            f"domain: {domain_name}\n"
            f"pack: core\n"
            f"category: {category}\n"
            f'version: "1.0.0"\n'
            f'description: "{name} agent"\n'
            f"\n"
            f"system_prompt_ref: system_prompt.md\n"
            f"\n"
            f"execution_mode:\n"
            f"  primary: react\n"
            f"  max_react_steps: 10\n"
            f"\n"
            f"tools:\n"
            f"  - name: file_read\n"
            f'    pack: "toolpack://core/filesystem"\n'
            f"\n"
            f"permissions:\n"
            f"  file_edit: deny\n"
            f"  shell_command: deny\n"
        )
        manifest_path = agent_dir / "agent_manifest.yaml"
        manifest_path.write_text(manifest_content, encoding="utf-8")

        prompt_content = f"You are {name}.\n\n## Role\nDescribe the agent role here.\n"
        prompt_path = agent_dir / "system_prompt.md"
        prompt_path.write_text(prompt_content, encoding="utf-8")

        logger.info("Scaffolded agent '%s' at %s", name, agent_dir)
        return {
            "agent_dir": str(agent_dir),
            "files_created": [str(manifest_path), str(prompt_path)],
        }

    def scaffold_workflow(
        self, domain_path: Path, name: str
    ) -> dict[str, Any]:
        """Scaffold a new workflow definition."""
        workflows_dir = domain_path / "workflows"
        workflows_dir.mkdir(parents=True, exist_ok=True)

        domain_name = domain_path.name

        wf_content = (
            f"name: {name}\n"
            f"domain: {domain_name}\n"
            f'version: "1.0.0"\n'
            f'description: "{name} workflow"\n'
            f"\n"
            f"steps:\n"
            f"  - name: step1\n"
            f'    agent: {domain_name}/AgentName/v1\n'
            f'    description: "First step"\n'
            f"\n"
            f"budget:\n"
            f"  max_tokens: 50000\n"
            f"  max_cost_usd: 2.0\n"
        )
        wf_path = workflows_dir / f"{name}.yaml"
        wf_path.write_text(wf_content, encoding="utf-8")

        logger.info("Scaffolded workflow '%s' at %s", name, wf_path)
        return {
            "workflow_file": str(wf_path),
            "files_created": [str(wf_path)],
        }

    def scaffold_pack(
        self, domain_path: Path, name: str
    ) -> dict[str, Any]:
        """Scaffold a new capability pack."""
        pack_dir = domain_path / "packs" / name
        pack_dir.mkdir(parents=True, exist_ok=True)

        pack_content = (
            f"name: {name}\n"
            f'version: "1.0.0"\n'
            f'description: "{name} capability pack"\n'
            f"\n"
            f"tools: []\n"
        )
        pack_path = pack_dir / "pack.yaml"
        pack_path.write_text(pack_content, encoding="utf-8")

        logger.info("Scaffolded pack '%s' at %s", name, pack_dir)
        return {
            "pack_dir": str(pack_dir),
            "files_created": [str(pack_path)],
        }

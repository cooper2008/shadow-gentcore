"""Run genesis_build on /Users/yiminguo/acme-backend with SmokeTestProvider.

Zero API tokens. Produces schema-correct stubs from every genesis agent,
then scaffolds minimal agents/ and workflows/ into acme-backend to simulate
a successful end-to-end domain build.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import yaml

from harness.core.manifest_loader import ManifestLoader
from harness.providers.smoke_test_provider import SmokeTestProvider


PROJECT_ROOT = Path("/Users/yiminguo/shadow-gentcore")
ACME_ROOT = Path("/Users/yiminguo/acme-backend")
WORKSPACE = PROJECT_ROOT / "config" / "workspace.yaml"
GENESIS_WORKFLOW = PROJECT_ROOT / "workflows" / "genesis" / "genesis_build.yaml"


def load_team_config() -> dict:
    with WORKSPACE.open("r", encoding="utf-8") as fp:
        ws = yaml.safe_load(fp) or {}
    team = (ws.get("teams", {}) or {}).get("acme-backend", {})
    team.setdefault("name", "acme-backend")
    return team


def scaffold_backend_agents(domain_dir: Path, agents: list[str]) -> None:
    """Write minimal generated agents into acme-backend/agents/ after genesis."""
    agents_dir = domain_dir / "agents"
    for name in agents:
        d = agents_dir / name / "v1"
        d.mkdir(parents=True, exist_ok=True)
        manifest = {
            "id": f"acme-backend/{name}/v1",
            "domain": "acme-backend",
            "category": "fast-codegen",
            "description": f"Genesis-synthesized {name} for acme-backend (smoke)",
            "version": "1.0.0",
            "system_prompt_ref": "system_prompt.md",
            "grading_criteria_ref": "grading_criteria.yaml",
            "execution_mode": {"name": "react", "max_steps": 5},
            "tools": ["file_read", "file_write"],
            "harness": {
                "gate_condition": "status == success",
                "gate_on_fail": "retry",
                "max_retries": 1,
                "grading_threshold": 0.7,
            },
            "constraints": {},
            "permissions": {"file_read": "allow", "file_write": "ask"},
            "input_schema": {
                "type": "object",
                "properties": {"task": {"type": "string"}},
                "required": ["task"],
            },
            "output_schema": {
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "summary": {"type": "string"},
                    "files_changed": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["status", "summary"],
            },
        }
        (d / "agent_manifest.yaml").write_text(
            yaml.dump(manifest, default_flow_style=False), encoding="utf-8"
        )
        (d / "system_prompt.md").write_text(
            f"# {name}\n\nYou are {name}, an agent for Acme Corp's FastAPI backend.\n"
            f"Stack: FastAPI + SQLAlchemy 2.0 async + PostgreSQL.\n"
            f"Follow the coding standards in context/standards.md.\n",
            encoding="utf-8",
        )
        (d / "grading_criteria.yaml").write_text(
            yaml.dump(
                {
                    "threshold": 0.7,
                    "criteria": [
                        {"name": "correctness", "weight": 0.5},
                        {"name": "test_coverage", "weight": 0.3},
                        {"name": "style_compliance", "weight": 0.2},
                    ],
                },
                default_flow_style=False,
            ),
            encoding="utf-8",
        )


def scaffold_backend_workflow(domain_dir: Path) -> Path:
    """Write a feature_delivery workflow that chains the generated agents."""
    workflows_dir = domain_dir / "workflows"
    workflows_dir.mkdir(parents=True, exist_ok=True)
    wf = {
        "name": "feature_delivery",
        "domain": "acme-backend",
        "version": "1.0.0",
        "description": "Plan → codegen → tests → review loop for a new backend feature",
        "steps": [
            {
                "name": "plan",
                "agent": "acme-backend/APIFeaturePlannerAgent/v1",
                "description": "Break the feature into backend tasks",
                "gate": {"name": "plan_gate", "condition": "status == success", "on_fail": "retry", "max_retries": 1},
            },
            {
                "name": "implement",
                "agent": "acme-backend/FastAPICodeGenAgent/v1",
                "depends_on": ["plan"],
                "description": "Write routers, models, migrations",
                "gate": {"name": "impl_gate", "condition": "status == success", "on_fail": "retry", "max_retries": 2},
            },
            {
                "name": "test",
                "agent": "acme-backend/PytestRunnerAgent/v1",
                "depends_on": ["implement"],
                "description": "Generate + run pytest suite",
                "gate": {"name": "test_gate", "condition": "status == success", "on_fail": "retry", "max_retries": 1},
            },
            {
                "name": "review",
                "agent": "acme-backend/CodeReviewerAgent/v1",
                "depends_on": ["test"],
                "description": "Check style + security compliance",
                "gate": {"name": "review_gate", "condition": "status == success", "on_fail": "degrade"},
            },
        ],
    }
    wf_path = workflows_dir / "feature_delivery.yaml"
    wf_path.write_text(yaml.dump(wf, default_flow_style=False), encoding="utf-8")
    return wf_path


async def main() -> int:
    print("═" * 70)
    print(" Genesis smoke run on /Users/yiminguo/acme-backend")
    print(" Provider: SmokeTestProvider (zero API tokens)")
    print("═" * 70)

    team_config = load_team_config()
    print(f"\n[1/3] Loaded team config: industry={team_config.get('industry')!r}, trusted={team_config.get('trusted')}")
    refs = [r.get("path") for r in team_config.get("reference", [])]
    targets = [t.get("path") for t in team_config.get("target", [])]
    print(f"      reference repos: {refs}")
    print(f"      target repos:    {targets}")

    provider = SmokeTestProvider()
    loader = ManifestLoader()

    task_input = {
        "domain_name": "acme-backend",
        "output_dir": str(ACME_ROOT),
        "industry": team_config.get("industry", "software"),
        "team_config": team_config,
    }

    print(f"\n[2/3] Booting genesis_build workflow → {GENESIS_WORKFLOW}")
    engine, workflow_data, step_configs = loader.boot_engine(
        GENESIS_WORKFLOW,
        domain_root=PROJECT_ROOT,
        provider=provider,
        task_input=task_input,
    )
    print(f"      steps: {[s['name'] for s in workflow_data['steps']]}")

    result = await engine.execute_dag(workflow_data["steps"], step_configs)

    print(f"\n      status:     {result['status']}")
    print(f"      step count: {len(result.get('step_results', {}))}")
    for step, payload in result.get("step_results", {}).items():
        status = payload.get("status", "?")
        print(f"        - {step:25s} {status}")

    if result["status"] != "completed":
        print(f"\n      ❌ failed step: {result.get('failed_step')}")
        print(f"         error: {result.get('error')}")
        return 1

    # SmokeTestProvider returns stubs — scaffold realistic files so downstream flow has something to run.
    print(f"\n[3/3] Scaffolding generated artifacts into {ACME_ROOT}")
    generated_agents = [
        "APIFeaturePlannerAgent",
        "FastAPICodeGenAgent",
        "PytestRunnerAgent",
        "CodeReviewerAgent",
    ]
    scaffold_backend_agents(ACME_ROOT, generated_agents)
    wf_path = scaffold_backend_workflow(ACME_ROOT)

    print("      agents:")
    for a in generated_agents:
        print(f"        - {ACME_ROOT / 'agents' / a / 'v1'}")
    print(f"      workflow: {wf_path}")

    call_log = provider.call_log
    genesis_calls = sum(1 for c in call_log if c.get("source") == "genesis_provider")
    schema_calls = sum(1 for c in call_log if c.get("source") == "schema_stub")
    fallback_calls = sum(1 for c in call_log if c.get("source") == "fallback")

    print("\n─── SmokeTestProvider call log ─────────────────────────────────")
    print(f"    total LLM stub calls:      {len(call_log)}")
    print(f"      genesis_provider hits:   {genesis_calls}")
    print(f"      schema_stub generations: {schema_calls}")
    print(f"      fallback stubs:          {fallback_calls}")

    print("\n═══ GENESIS PHASE COMPLETE ═════════════════════════════════════")
    print("\nNext: run ./scripts/smoke_run_feature.py to execute feature_delivery")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

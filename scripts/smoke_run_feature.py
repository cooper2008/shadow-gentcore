"""Run the generated feature_delivery workflow on acme-backend.

Simulates a real feature request (GET /items/{id}/reviews with pagination)
going through plan → implement → test → review using the 4 synthesized
backend agents. Uses SmokeTestProvider (zero API tokens).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from harness.core.manifest_loader import ManifestLoader
from harness.providers.smoke_test_provider import SmokeTestProvider


PROJECT_ROOT = Path("/Users/yiminguo/shadow-gentcore")
ACME_ROOT = Path("/Users/yiminguo/acme-backend")
FEATURE_WF = ACME_ROOT / "workflows" / "feature_delivery.yaml"


TASK_PAYLOAD = {
    "feature": "Add GET /items/{item_id}/reviews endpoint with cursor pagination",
    "acceptance_criteria": [
        "Route returns 200 with list of reviews for a given item",
        "Pagination via ?cursor=<b64> and ?limit=<int, default=20, max=100>",
        "Response includes `next_cursor` when more pages exist",
        "Unit tests cover happy path + invalid item_id + empty reviews list",
    ],
    "repo_context": {
        "stack": "FastAPI + SQLAlchemy 2.0 async + PostgreSQL",
        "models_path": "src/acme_api/models",
        "routers_path": "src/acme_api/routers",
        "tests_path": "tests",
    },
}


async def main() -> int:
    print("═" * 70)
    print(" feature_delivery workflow on acme-backend")
    print(" Provider: SmokeTestProvider (zero API tokens)")
    print("═" * 70)

    if not FEATURE_WF.exists():
        print(f"\n❌ Workflow not found: {FEATURE_WF}")
        print("   Run scripts/smoke_genesis_acme.py first to generate it.")
        return 1

    print(f"\n[1/2] Loading workflow: {FEATURE_WF}")
    print(f"      Task: {TASK_PAYLOAD['feature']}")

    provider = SmokeTestProvider()
    loader = ManifestLoader()

    task_input = {
        "task": TASK_PAYLOAD["feature"],
        "feature": TASK_PAYLOAD["feature"],
        "acceptance_criteria": TASK_PAYLOAD["acceptance_criteria"],
        "repo_context": TASK_PAYLOAD["repo_context"],
    }

    engine, workflow_data, step_configs = loader.boot_engine(
        FEATURE_WF,
        domain_root=ACME_ROOT,
        provider=provider,
        task_input=task_input,
    )

    print(f"      Workflow: {workflow_data.get('name')} v{workflow_data.get('version')}")
    step_names = [s["name"] for s in workflow_data["steps"]]
    print(f"      Steps:    {step_names}")

    print("\n[2/2] Executing DAG...")
    result = await engine.execute_dag(workflow_data["steps"], step_configs)

    print(f"\n      overall status: {result['status']}")
    print(f"      step count:     {len(result.get('step_results', {}))}")
    print()
    for step_name in step_names:
        payload = result.get("step_results", {}).get(step_name, {})
        status = payload.get("status", "not_run")
        marker = "✓" if status == "completed" else ("✗" if status in ("failed", "error") else "·")
        print(f"      {marker} {step_name:12s}  {status}")

    if result["status"] != "completed":
        print(f"\n      failed step: {result.get('failed_step')}")
        print(f"      error:       {result.get('error')}")
        return 1

    # Show first step's sample output to prove schema-correct flow
    sample = result["step_results"][step_names[0]].get("output", {})
    print("\n─── sample plan-step output (stub, schema-correct) ────────────")
    from pprint import pformat
    pretty = pformat(sample, width=66)
    for line in pretty.splitlines()[:12]:
        print("    " + line)

    call_log = provider.call_log
    print("\n─── SmokeTestProvider call log ─────────────────────────────────")
    print(f"    total stub calls: {len(call_log)}")
    sources: dict[str, int] = {}
    for c in call_log:
        src = c.get("source", "unknown")
        sources[src] = sources.get(src, 0) + 1
    for src, n in sources.items():
        print(f"      {src}: {n}")

    print("\n═══ FEATURE WORKFLOW COMPLETE ═══════════════════════════════════")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

"""Run the generated feature_delivery workflow on acme-backend for real,
using Minimax m2.7 via the Anthropic-compatible endpoint.

This is the USER-POV test — plan → implement → test → review all run
through real LLM calls. Proves the framework's DAG, gate, and provider
layers work end-to-end against a live model.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from harness.core.manifest_loader import ManifestLoader
from harness.providers.anthropic_provider import AnthropicProvider


ACME_ROOT = Path("/Users/yiminguo/acme-backend")
FEATURE_WF = ACME_ROOT / "workflows" / "feature_delivery.yaml"


TASK = {
    "task": "Add GET /items/{item_id}/reviews endpoint with cursor pagination",
    "feature": "GET /items/{item_id}/reviews with cursor pagination",
    "acceptance_criteria": [
        "Returns 200 with list of reviews ordered by created_at desc",
        "?cursor=<b64>&limit=<int,default=20,max=100>",
        "Response includes next_cursor when more pages exist",
        "Unit tests cover happy path + invalid item_id + empty reviews",
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
    print(" USER-POV test: feature_delivery workflow on acme-backend")
    print(f"   base_url: {os.environ.get('ANTHROPIC_BASE_URL', '(unset)')}")
    print("   model:    m2.7 (Minimax)")
    print("═" * 70)

    for var in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"):
        if not os.environ.get(var):
            print(f"\n❌ {var} not set.")
            return 1

    provider = AnthropicProvider(model="m2.7", max_tokens=1024)
    loader = ManifestLoader()

    print(f"\n[1/2] Loading workflow: {FEATURE_WF}")
    engine, workflow_data, step_configs = loader.boot_engine(
        FEATURE_WF,
        domain_root=ACME_ROOT,
        provider=provider,
        task_input=TASK,
    )
    step_names = [s["name"] for s in workflow_data["steps"]]
    print(f"      steps: {step_names}")
    print(f"      task:  {TASK['task']}")

    print("\n[2/2] Executing DAG against live model (this may take 60-90s)...")
    result = await engine.execute_dag(workflow_data["steps"], step_configs)

    print(f"\n─── DAG COMPLETE ────────────────────────────────────────────────")
    print(f"   overall status: {result['status']}")
    if result.get("failed_step"):
        print(f"   failed step:    {result['failed_step']}")
        print(f"   error:          {result.get('error')}")

    for step in step_names:
        payload = result.get("step_results", {}).get(step, {})
        status = payload.get("status", "not_run")
        marker = "✓" if status == "completed" else ("✗" if status in ("failed", "error") else "·")
        print(f"   {marker} {step:12s}  {status}")

    print("\n─── PER-STEP OUTPUTS ───────────────────────────────────────────")
    for step in step_names:
        payload = result.get("step_results", {}).get(step, {})
        output = payload.get("output")
        print(f"\n   ─ {step} ─")
        try:
            pretty = json.dumps(output, indent=2) if isinstance(output, (dict, list)) else str(output)
        except Exception:
            pretty = str(output)
        for line in (pretty or "(none)").splitlines()[:15]:
            print(f"     {line}")

    print("\n═══ USER-POV TEST DONE ══════════════════════════════════════════")
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

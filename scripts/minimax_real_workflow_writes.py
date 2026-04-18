"""End-to-end USER-POV test: run the generated feature_delivery workflow on
acme-backend with real LLM (Minimax m2.7), and have the agents actually
WRITE source files to disk via the file_write tool.

Files are created in an isolated sandbox so we can inspect what the agent
framework produced without mixing it with the real acme-backend repo.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

from harness.core.manifest_loader import ManifestLoader
from harness.providers.anthropic_provider import AnthropicProvider


ACME_ROOT = Path("/Users/yiminguo/acme-backend")
FEATURE_WF = ACME_ROOT / "workflows" / "feature_delivery.yaml"
SANDBOX = Path("/tmp/acme-reviews-sandbox")


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


def prepare_sandbox() -> None:
    if SANDBOX.exists():
        shutil.rmtree(SANDBOX)
    SANDBOX.mkdir(parents=True)
    # Seed a minimal project layout so agents know where to write
    for sub in ("src/acme_api/models", "src/acme_api/schemas", "src/acme_api/services",
                "src/acme_api/routers", "src/acme_api/database", "migrations/versions", "tests"):
        (SANDBOX / sub).mkdir(parents=True, exist_ok=True)
    (SANDBOX / "src/acme_api/__init__.py").write_text("", encoding="utf-8")
    (SANDBOX / "src/acme_api/database/__init__.py").write_text("", encoding="utf-8")
    (SANDBOX / "src/acme_api/database/base.py").write_text(
        "from sqlalchemy.orm import DeclarativeBase\n\nclass Base(DeclarativeBase):\n    pass\n",
        encoding="utf-8",
    )


def list_sandbox_files() -> list[Path]:
    return sorted(p for p in SANDBOX.rglob("*") if p.is_file())


async def main() -> int:
    print("═" * 74)
    print(" USER-POV end-to-end test — agents actually write source files")
    print(f"   sandbox: {SANDBOX}")
    print(f"   model:   m2.7 (Minimax via ANTHROPIC_BASE_URL)")
    print("═" * 74)

    for var in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"):
        if not os.environ.get(var):
            print(f"\n❌ {var} not set.")
            return 1

    print("\n[1/3] Preparing sandbox...")
    prepare_sandbox()
    baseline = list_sandbox_files()
    print(f"      seeded {len(baseline)} files (Base class, __init__.py, dirs)")

    provider = AnthropicProvider(model="m2.7", max_tokens=2048)
    loader = ManifestLoader()

    print(f"\n[2/3] Booting feature_delivery workflow (in sandbox cwd)...")
    engine, workflow_data, step_configs = loader.boot_engine(
        FEATURE_WF,
        domain_root=ACME_ROOT,
        provider=provider,
        task_input=TASK,
    )
    step_names = [s["name"] for s in workflow_data["steps"]]
    print(f"      steps: {step_names}")
    print(f"      task:  {TASK['task']}")

    print("\n[3/3] Executing DAG against live Minimax m2.7 (90-180s)...")
    print("      (each agent can call file_write; files land in sandbox cwd)\n")

    original_cwd = os.getcwd()
    try:
        os.chdir(SANDBOX)
        result = await engine.execute_dag(workflow_data["steps"], step_configs)
    finally:
        os.chdir(original_cwd)

    print(f"\n─── DAG RESULT ──────────────────────────────────────────────────────")
    print(f"   overall status: {result['status']}")
    if result.get("error"):
        print(f"   error:          {result['error']}")
    if result.get("failed_step"):
        print(f"   failed step:    {result['failed_step']}")
    for step in step_names:
        payload = result.get("step_results", {}).get(step, {})
        status = payload.get("status", "not_run")
        marker = "✓" if status == "completed" else ("✗" if status in ("failed", "error") else "·")
        print(f"   {marker} {step:12s}  {status}")

    print("\n─── EXECUTION LOG (gates, retries, feedback loops) ─────────────────")
    for entry in result.get("execution_log", []):
        event = entry.get("event", "?")
        if hasattr(event, "value"):
            event = event.value
        step = entry.get("step", entry.get("step_name", entry.get("from_step", "")))
        detail = {k: v for k, v in entry.items() if k not in ("event", "step", "step_name", "from_step")}
        print(f"   [{event:25s}] {step:15s} {detail}")

    print("\n─── PER-STEP OUTPUT KEYS ───────────────────────────────────────────")
    for step in step_names:
        payload = result.get("step_results", {}).get(step, {})
        output = payload.get("output")
        if isinstance(output, dict):
            keys = list(output.keys())
            sig = {k: output[k] for k in keys if k in ("all_passed", "approved", "status", "errors", "issues")}
            print(f"   {step:12s}  output_keys={keys}  signals={sig}")
        else:
            print(f"   {step:12s}  output_type={type(output).__name__}")

    final = list_sandbox_files()
    new_files = sorted(set(final) - set(baseline))

    print(f"\n─── FILES WRITTEN ({len(new_files)} new) ──────────────────────────")
    for p in new_files:
        rel = p.relative_to(SANDBOX)
        size = p.stat().st_size
        print(f"   {size:6d}B  {rel}")

    if new_files:
        first = new_files[0]
        print(f"\n─── SAMPLE CONTENT: {first.relative_to(SANDBOX)} ─────────────")
        lines = first.read_text(encoding="utf-8").splitlines()
        for line in lines[:40]:
            print(f"   {line}")
        if len(lines) > 40:
            print(f"   ... ({len(lines) - 40} more lines)")

    print("\n─── PER-STEP SUMMARIES ─────────────────────────────────────────────")
    for step in step_names:
        payload = result.get("step_results", {}).get(step, {})
        output = payload.get("output")
        if isinstance(output, dict):
            summary = output.get("summary", "")
            files = output.get("files_changed", [])
            print(f"\n   ─ {step} ─")
            print(f"     files_changed: {files}")
            first_line = summary.splitlines()[0] if summary else ""
            print(f"     summary head: {first_line[:140]}")

    print("\n═══ USER-POV TEST DONE ══════════════════════════════════════════════")
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

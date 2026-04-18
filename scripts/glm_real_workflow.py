"""Run the feature_delivery workflow on acme-backend using GLM-5.1
(via BigModel's Anthropic-compatible endpoint).
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
SANDBOX = Path("/tmp/acme-reviews-sandbox-glm")

GLM_KEY = "86e8d66570984003974410bf3433f22b.QksBbeE9LCMkX1hY"
GLM_BASE = "https://open.bigmodel.cn/api/anthropic"
GLM_MODEL = "glm-5.1"


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
    for sub in ("src/acme_api/models", "src/acme_api/schemas", "src/acme_api/services",
                "src/acme_api/routers", "src/acme_api/database", "migrations/versions", "tests"):
        (SANDBOX / sub).mkdir(parents=True, exist_ok=True)
    (SANDBOX / "src/acme_api/__init__.py").write_text("", encoding="utf-8")
    (SANDBOX / "src/acme_api/database/__init__.py").write_text("", encoding="utf-8")
    (SANDBOX / "src/acme_api/database/base.py").write_text(
        "from sqlalchemy.orm import DeclarativeBase\n\nclass Base(DeclarativeBase):\n    pass\n",
        encoding="utf-8",
    )
    # Make acme_api importable without pip install — conftest.py prepends src/
    (SANDBOX / "tests/conftest.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))\n",
        encoding="utf-8",
    )
    # pytest.ini enables asyncio + tells pytest where tests live
    (SANDBOX / "pytest.ini").write_text(
        "[pytest]\nasyncio_mode = auto\ntestpaths = tests\npython_files = test_*.py\n",
        encoding="utf-8",
    )


def list_files() -> list[Path]:
    return sorted(p for p in SANDBOX.rglob("*")
                  if p.is_file() and "__pycache__" not in str(p) and ".pytest_cache" not in str(p))


async def main() -> int:
    print("═" * 74)
    print(" USER-POV end-to-end test — GLM-5.1 via BigModel")
    print(f"   sandbox: {SANDBOX}")
    print(f"   model:   {GLM_MODEL}")
    print(f"   base:    {GLM_BASE}")
    print("═" * 74)

    print("\n[1/3] Preparing sandbox...")
    prepare_sandbox()
    baseline = list_files()
    print(f"      seeded {len(baseline)} files")

    provider = AnthropicProvider(
        auth_token=GLM_KEY,
        base_url=GLM_BASE,
        model=GLM_MODEL,
        max_tokens=4096,
    )
    loader = ManifestLoader()

    print(f"\n[2/3] Booting feature_delivery workflow ...")
    engine, workflow_data, step_configs = loader.boot_engine(
        FEATURE_WF,
        domain_root=ACME_ROOT,
        provider=provider,
        task_input=TASK,
    )
    step_names = [s["name"] for s in workflow_data["steps"]]
    print(f"      steps: {step_names}")

    print("\n[3/3] Executing DAG (2-5 min)...")
    original_cwd = os.getcwd()
    try:
        os.chdir(SANDBOX)
        result = await engine.execute_dag(workflow_data["steps"], step_configs)
    finally:
        os.chdir(original_cwd)

    print(f"\n─── DAG RESULT ──────────────────────────────────────────────────")
    print(f"   overall status: {result['status']}")
    if result.get("error"):
        print(f"   error:          {result['error']}")
    for step in step_names:
        payload = result.get("step_results", {}).get(step, {})
        status = payload.get("status", "not_run")
        marker = "✓" if status == "completed" else ("✗" if status in ("failed", "error") else "·")
        print(f"   {marker} {step:12s}  {status}")

    print("\n─── EXECUTION LOG ─────────────────────────────────────────────")
    for entry in result.get("execution_log", []):
        event = entry.get("event", "?")
        if hasattr(event, "value"):
            event = event.value
        step = entry.get("step", entry.get("from_step", ""))
        detail = {k: v for k, v in entry.items() if k not in ("event", "step", "from_step")}
        print(f"   [{event:25s}] {step:12s} {detail}")

    print("\n─── PER-STEP SIGNALS ───────────────────────────────────────────")
    for step in step_names:
        payload = result.get("step_results", {}).get(step, {})
        output = payload.get("output")
        if isinstance(output, dict):
            sig = {k: output[k] for k in ("all_passed", "approved", "status", "summary")
                   if k in output}
            if "summary" in sig:
                sig["summary"] = str(sig["summary"])[:120]
            print(f"   {step:12s}  {sig}")
        else:
            print(f"   {step:12s}  output_type={type(output).__name__}")

    final = list_files()
    new_files = sorted(set(final) - set(baseline))
    print(f"\n─── FILES WRITTEN ({len(new_files)}) ──────────────────────────")
    for p in new_files:
        rel = p.relative_to(SANDBOX)
        size = p.stat().st_size
        print(f"   {size:6d}B  {rel}")

    print("\n═══ DONE ══════════════════════════════════════════════════════")
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

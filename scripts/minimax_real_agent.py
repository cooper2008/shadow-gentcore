"""Run a single acme-backend agent for real, using Minimax via the
Anthropic-compatible endpoint.

Routes the AnthropicProvider through MINIMAX's ``/anthropic`` gateway
using the ANTHROPIC_BASE_URL + ANTHROPIC_AUTH_TOKEN env vars. Model: m2.7.

Small test — one agent, one task — to verify real LLM integration
without burning a full genesis run.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from harness.core.manifest_loader import ManifestLoader
from harness.core.agent_runner import AgentRunner
from harness.providers.anthropic_provider import AnthropicProvider


ACME_ROOT = Path("/Users/yiminguo/acme-backend")
AGENT_DIR = ACME_ROOT / "agents" / "FastAPICodeGenAgent" / "v1"


async def main() -> int:
    print("═" * 70)
    print(" Real-LLM agent run via Minimax (Anthropic-compatible)")
    print(f"   base_url: {os.environ.get('ANTHROPIC_BASE_URL', '(unset)')}")
    print("   model:    m2.7")
    print("═" * 70)

    if not os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        print("\n❌ ANTHROPIC_AUTH_TOKEN not set.")
        return 1
    if not os.environ.get("ANTHROPIC_BASE_URL"):
        print("\n❌ ANTHROPIC_BASE_URL not set.")
        return 1

    provider = AnthropicProvider(model="m2.7", max_tokens=1024)

    loader = ManifestLoader()
    manifest, system_prompt, context_items = loader.load_agent(AGENT_DIR)
    print(f"\n[1/2] Loaded agent: {manifest.get('id')}")
    print(f"      category:      {manifest.get('category')}")
    print(f"      execution:     {manifest.get('execution_mode')}")
    print(f"      context items: {len(context_items)}")
    print(f"      prompt bytes:  {len(system_prompt)}")

    runner = AgentRunner(provider=provider)
    task = {
        "task": (
            "Briefly describe how you would add a GET /items/{item_id}/reviews "
            "endpoint with cursor pagination to a FastAPI + SQLAlchemy backend. "
            "Reply in 3 bullet points only."
        )
    }

    print(f"\n[2/2] Running agent on task:\n  > {task['task']}\n")

    try:
        result = await runner.run(
            manifest=manifest,
            task=task,
            system_prompt_content=system_prompt,
            context_items=context_items,
        )
    except Exception as exc:
        print(f"❌ Agent failed: {type(exc).__name__}: {exc}")
        import traceback
        traceback.print_exc()
        return 1

    print("─── RESULT ─────────────────────────────────────────────────────")
    print(f"   top-level keys: {list(result.keys())}")
    print(f"   status:         {result.get('status', '?')}")

    output = result.get("output")
    content = result.get("content")

    print("\n   structured output:")
    try:
        pretty = json.dumps(output, indent=2) if isinstance(output, (dict, list)) else str(output)
    except Exception:
        pretty = str(output)
    print(pretty[:2000] if pretty else "(none)")

    if content:
        print("\n   raw content (first 1500 chars):")
        print(str(content)[:1500])

    record = result.get("run_record", {})
    if isinstance(record, dict):
        tokens = record.get("tokens_used") or record.get("total_tokens")
        if tokens:
            print(f"\n   tokens_used: {tokens}")
        steps = record.get("react_steps") or record.get("steps")
        if steps:
            print(f"   steps:       {len(steps) if isinstance(steps, list) else steps}")

    print("\n═══ DONE ═══════════════════════════════════════════════════════")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

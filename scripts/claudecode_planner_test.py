"""Smoke-test: run the planner agent via ClaudeCodeProvider.

Uses the local `claude` CLI which talks to Claude Code's subscription —
no API key, no tokens from an API plan.

Only works for agents that don't need tool calls (file_write etc.) because
`claude -p` is one-shot text I/O.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from harness.core.manifest_loader import ManifestLoader
from harness.core.agent_runner import AgentRunner
from harness.providers.claudecode_provider import ClaudeCodeProvider


ACME = Path("/Users/yiminguo/acme-backend")
AGENT = ACME / "agents" / "APIFeaturePlannerAgent" / "v1"


async def main() -> int:
    print("─" * 70)
    print("Running planner via ClaudeCodeProvider (subscription, no API key)")
    print("─" * 70)

    loader = ManifestLoader()
    manifest, sp, ctx = loader.load_agent(AGENT)

    provider = ClaudeCodeProvider(timeout=180)
    runner = AgentRunner(provider=provider)

    try:
        result = await runner.run(
            manifest=manifest,
            task={"task": "Plan GET /items/{item_id}/reviews endpoint with cursor pagination. FastAPI + SQLAlchemy 2.0 async. List the files to create and what goes in each."},
            system_prompt_content=sp,
            context_items=ctx,
        )
    except Exception as exc:
        print(f"❌ Failed: {type(exc).__name__}: {exc}")
        return 1

    print(f"\nstatus: {result.get('status')}")
    content = result.get("content", "")
    print(f"\ncontent ({len(content)} chars):\n")
    print(content[:2500])

    out = result.get("output")
    if isinstance(out, dict):
        print(f"\nstructured output keys: {list(out.keys())}")
    else:
        print(f"\noutput type: {type(out).__name__}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

"""Directly invoke planner agent via Gemini, print the raw response."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from harness.core.manifest_loader import ManifestLoader
from harness.core.agent_runner import AgentRunner
from harness.core.tool_executor import ToolExecutor
from harness.providers.openai_provider import OpenAIProvider
from harness.tools.builtin import register_builtins


GEMINI_KEY = "AIzaSyDN3S7USdoh4HixURQgGovVDwH4NcKXYmY"
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/openai"

ACME = Path("/Users/yiminguo/acme-backend")
AGENT = ACME / "agents" / "APIFeaturePlannerAgent" / "v1"


async def main() -> int:
    os.environ["OPENAI_API_KEY"] = GEMINI_KEY
    os.environ["OPENAI_BASE_URL"] = GEMINI_BASE

    loader = ManifestLoader()
    manifest, sp, ctx = loader.load_agent(AGENT)
    print(f"agent: {manifest['id']}")
    print(f"tools: {manifest.get('tools')}")
    print(f"required: {manifest['output_schema'].get('required')}")

    provider = OpenAIProvider(
        api_key=GEMINI_KEY,
        model="gemini-3.1-pro-preview",
        max_tokens=4096,
        base_url=GEMINI_BASE,
    )
    te = ToolExecutor()
    register_builtins(te)
    runner = AgentRunner(provider=provider, tool_executor=te)

    result = await runner.run(
        manifest=manifest,
        task={"task": "Plan for GET /items/{id}/reviews with cursor pagination. FastAPI + SQLAlchemy async."},
        system_prompt_content=sp,
        context_items=ctx,
    )

    print("\n=== KEYS ===")
    for k in result:
        v = result[k]
        print(f"  {k:22s} = {type(v).__name__}{' = ' + str(v)[:80] if not isinstance(v, (dict, list)) else ''}")

    print("\n=== CONTENT (first 1500) ===")
    print(str(result.get("content", ""))[:1500])

    print("\n=== OUTPUT ===")
    out = result.get("output")
    if isinstance(out, dict):
        print(f"  keys: {list(out.keys())}")
        for k, v in out.items():
            sv = str(v)
            print(f"  {k}: {sv[:100]}")
    else:
        print(f"  type={type(out).__name__}  value[:500]={str(out)[:500]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

"""Debug: run implement step via Gemini, print RAW content from provider."""

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
AGENT = ACME / "agents" / "FastAPICodeGenAgent" / "v1"


async def main() -> int:
    os.environ["OPENAI_API_KEY"] = GEMINI_KEY
    os.environ["OPENAI_BASE_URL"] = GEMINI_BASE

    loader = ManifestLoader()
    manifest, sp, ctx = loader.load_agent(AGENT)

    sandbox = Path("/tmp/acme-debug-impl")
    sandbox.mkdir(parents=True, exist_ok=True)
    os.chdir(sandbox)

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
        task={
            "task": "Create one file: src/models/review.py with a simple Review model. Call file_write once. Then emit JSON with status, summary, files_changed.",
        },
        system_prompt_content=sp,
        context_items=ctx,
    )

    print(f"status: {result.get('status')}")
    print(f"error: {result.get('error')}")
    print()
    print(f"=== PARSED_OUTPUT === (from OutputParser)")
    po = result.get("result", {}).get("parsed_output") if isinstance(result.get("result"), dict) else None
    print(repr(po)[:800])
    print()
    print("=== CONTENT (first 1500) ===")
    content = result.get("content", "")
    print(str(content)[:1500])
    print()
    print("=== OUTPUT ===")
    out = result.get("output")
    print(f"  type: {type(out).__name__}")
    if isinstance(out, str):
        print(f"  value[:500]: {out[:500]}")
    elif isinstance(out, dict):
        print(f"  keys: {list(out.keys())}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

"""Diagnostic: does GLM-5.1 via BigModel honor tool_choice=force?

Run: .venv/bin/python scripts/glm_probe.py

Probes 3 paths through anthropic_provider.py with a real GLM-5.1 endpoint:
  1. forced submit_output (tools=None) — must return submit_output_fired=True
  2. tools=[] (was-buggy path) — same expectation after the fix
  3. coexist (real tools + schema) — submit_output may or may not fire

If GLM ignores tool_choice, paths 1 and 2 will return content="{}" and
submit_output_fired=False — proving the framework fix can't help and the
real fix has to live above the provider (e.g. fallback to Claude or
parse-and-coerce).
"""
import asyncio
import os
import sys

sys.path.insert(0, "/Users/yiminguo/shadow-gentcore")
sys.path.insert(0, "/Users/yiminguo/agent-contracts/src")

from harness.providers.anthropic_provider import AnthropicProvider


async def main() -> None:
    api_key = os.environ.get("ZHIPU_API_KEY")
    if not api_key:
        print("ZHIPU_API_KEY not set; aborting probe.")
        sys.exit(2)

    prov = AnthropicProvider(
        api_key=api_key,
        model="glm-5.1",
        base_url="https://open.bigmodel.cn/api/anthropic",
        max_tokens=1024,
    )

    schema = {
        "type": "object",
        "required": ["agent_roster"],
        "properties": {
            "agent_roster": {"type": "array", "items": {"type": "object"}},
        },
    }

    print("[probe 1] forced submit_output (tools=None, schema set)")
    resp = await prov.chat(
        [{"role": "user", "content": "Design a minimal agent roster of 2 agents for a FastAPI backend service. Return the result via the submit_output tool."}],
        output_schema=schema,
    )
    print(f"  content[:300]: {resp.content[:300]!r}")
    print(f"  tool_calls   : {resp.tool_calls}")
    print(f"  fired        : {resp.raw.get('submit_output_fired')}")

    print("\n[probe 2] empty-tools-list (was buggy path, schema set)")
    resp = await prov.chat(
        [{"role": "user", "content": "Design a minimal agent roster of 2 agents for a FastAPI backend service. Return the result via submit_output."}],
        tools=[],
        output_schema=schema,
    )
    print(f"  content[:300]: {resp.content[:300]!r}")
    print(f"  tool_calls   : {resp.tool_calls}")
    print(f"  fired        : {resp.raw.get('submit_output_fired')}")


if __name__ == "__main__":
    asyncio.run(main())

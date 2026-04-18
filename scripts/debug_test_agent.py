"""Debug: run PytestRunnerAgent directly, print wrapper keys + validator result."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from harness.core.manifest_loader import ManifestLoader
from harness.core.agent_runner import AgentRunner
from harness.core.output_validator import OutputValidator
from harness.core.tool_executor import ToolExecutor
from harness.providers.anthropic_provider import AnthropicProvider
from harness.tools.builtin import register_builtins


ACME = Path("/Users/yiminguo/acme-backend")
AGENT = ACME / "agents" / "PytestRunnerAgent" / "v1"


async def main() -> int:
    loader = ManifestLoader()
    manifest, sp, ctx = loader.load_agent(AGENT)
    print(f"agent: {manifest['id']}")
    print(f"output_schema required: {manifest['output_schema']['required']}")

    os.chdir("/tmp/acme-reviews-sandbox")
    provider = AnthropicProvider(model="m2.7", max_tokens=1024)
    te = ToolExecutor()
    register_builtins(te)
    runner = AgentRunner(provider=provider, tool_executor=te)

    result = await runner.run(
        manifest=manifest,
        task={"task": "Check if tests/ dir exists; if empty, report all_passed=false."},
        system_prompt_content=sp,
        context_items=ctx,
    )

    print("\n=== TOP LEVEL WRAPPER KEYS ===")
    for k in result:
        v = result[k]
        print(f"  {k:20s} = {type(v).__name__}{' = ' + str(v)[:60] if not isinstance(v, dict) and not isinstance(v, list) else ''}")

    print("\n=== content (first 500 chars) ===")
    print(str(result.get("content", ""))[:500])

    print("\n=== output ===")
    out = result.get("output")
    print(f"  type: {type(out).__name__}")
    if isinstance(out, dict):
        print(f"  keys: {list(out.keys())}")

    validator = OutputValidator()
    validation = await validator.validate(result, manifest)
    print("\n=== VALIDATOR RESULT ===")
    print(f"  passed: {validation['passed']}")
    print(f"  schema_valid: {validation['schema_valid']}")
    print(f"  issues: {validation['issues']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

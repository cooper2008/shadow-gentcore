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
    # Default vendor = GLM-5.1; override with VENDOR=gemini for Gemini probe.
    vendor = os.environ.get("VENDOR", "glm")
    if vendor == "gemini":
        from harness.providers.openai_provider import OpenAIProvider
        google_key = os.environ.get("GOOGLE_API_KEY")
        if not google_key:
            print("GOOGLE_API_KEY not set; aborting Gemini probe.")
            sys.exit(2)
        prov = OpenAIProvider(
            api_key=google_key,
            model=os.environ.get("VENDOR_MODEL", "gemini-2.5-flash"),
            base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            max_tokens=1024,
        )
        print(f"[Gemini probe] model={prov._model}")
    else:
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

    # Architect-realistic probe: large system + huge nested schema
    print("\n[probe 3] architect-realistic: ~30K-token system + nested schema")
    big_schema = {
        "type": "object",
        "required": ["agent_roster", "workflow_designs", "design_quality"],
        "properties": {
            "agent_roster": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["name", "purpose", "category", "decision"],
                    "properties": {
                        "name": {"type": "string"},
                        "purpose": {"type": "string"},
                        "category": {"type": "string", "enum": ["reasoning", "fast-codegen", "ops"]},
                        "decision": {"type": "string", "enum": ["reuse-core", "synthesize-new"]},
                    },
                },
            },
            "workflow_designs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["name", "steps"],
                    "properties": {
                        "name": {"type": "string"},
                        "steps": {"type": "array"},
                    },
                },
            },
            "design_quality": {
                "type": "object",
                "required": ["agent_count", "dag_valid"],
                "properties": {
                    "agent_count": {"type": "integer"},
                    "dag_valid": {"type": "boolean"},
                },
            },
        },
    }
    big_system = (
        "You are AgentArchitectAgent v2. Design a domain's agent workflow.\n\n"
        + ("This is filler context to simulate the shared_stage_catalog and capabilities_config preloads. " * 1000)
    )
    resp = await prov.chat(
        [
            {"role": "system", "content": big_system},
            {"role": "user", "content": "Design 3 agents for a FastAPI backend: one to plan, one to write code, one to test. One workflow named feature_delivery."},
        ],
        output_schema=big_schema,
    )
    print(f"  prompt size  : ~{len(big_system) // 4} system tokens (rough)")
    print(f"  content[:400]: {resp.content[:400]!r}")
    print(f"  fired        : {resp.raw.get('submit_output_fired')}")

    # Probe 4: actual architect via the framework code path (react mode)
    print("\n[probe 4] real architect through ReActStrategy + manifest_loader")
    from pathlib import Path
    import yaml as _yaml
    from harness.core.modes.react import ReActStrategy

    project_root = Path("/Users/yiminguo/shadow-gentcore")
    arch_path = project_root / "agents" / "_genesis" / "AgentArchitectAgent" / "v2"
    manifest = _yaml.safe_load((arch_path / "agent_manifest.yaml").read_text())
    output_schema = manifest.get("output_schema")
    system_prompt = (arch_path / "system_prompt.md").read_text()

    # Simulate the preload context the architect normally gets
    from harness.core.manifest_loader import _preload_shared_stage_catalog, _preload_capabilities_config
    preloads = []
    for p in (_preload_shared_stage_catalog(), _preload_capabilities_config()):
        if p:
            preloads.append(p["content"])
    full_system = system_prompt + "\n\n" + "\n\n---\n\n".join(preloads)

    # Mock upstream input — reasonable shape
    user_input = """Design the agent roster + workflow_designs for a backend domain.
Inputs:
- knowledge_map: {"workflow_processes": [{"name": "feature_delivery"}]}
- context_docs: {"standards_md": "Follow PEP 8."}
- tools_discovered: {"tool_packs": ["filesystem", "shell"]}
- industry: backend
- stage_catalog: (from preload)
- capability_map: (from preload)
"""
    msgs = [
        {"role": "system", "content": full_system},
        {"role": "user", "content": user_input},
    ]
    print(f"  sys size    : ~{sum(len(m['content']) for m in msgs) // 4} tokens (rough)")
    strategy = ReActStrategy(max_steps=1)
    result = await strategy.execute(
        messages=msgs,
        provider=prov,
        tool_executor=None,
        output_schema=output_schema,
        declared_tools=[],
    )
    print(f"  content[:400]: {result.get('content', '')[:400]!r}")
    parsed = result.get("parsed_output") or {}
    print(f"  parsed keys : {list(parsed.keys())[:5]}")
    print(f"  steps       : {[s.get('type') for s in result.get('steps', [])]}")


if __name__ == "__main__":
    asyncio.run(main())

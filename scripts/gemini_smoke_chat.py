"""Smoke-test Gemini via OpenAI-compatible endpoint.

Fastest sanity check: single chat call, no agent framework overhead. If this
prints a response, the endpoint+key+model combo works and we can wire it
into AgentRunner / CompositionEngine.
"""

from __future__ import annotations

import asyncio
import os

from harness.providers.openai_provider import OpenAIProvider


async def main() -> int:
    key = "AIzaSyDN3S7USdoh4HixURQgGovVDwH4NcKXYmY"
    # Note: NO trailing slash — OpenAI SDK appends /chat/completions and a
    # double slash makes Gemini reject the request as API_KEY_INVALID.
    base = "https://generativelanguage.googleapis.com/v1beta/openai"

    os.environ["OPENAI_BASE_URL"] = base
    # Override any existing OPENAI_API_KEY (it would otherwise win precedence
    # and be sent to Gemini's endpoint → "API_KEY_INVALID").
    os.environ["OPENAI_API_KEY"] = key

    candidates = [
        "gemini-3.1-pro-preview",
        "gemini-3-pro-preview",
        "gemini-2.5-pro",
        "gemini-pro-latest",
    ]

    for model_id in candidates:
        print(f"\n─── trying {model_id} ──────────────────────────────")
        prov = OpenAIProvider(api_key=key, model=model_id, max_tokens=2048, base_url=base)
        try:
            resp = await prov.chat(
                messages=[
                    {"role": "system", "content": "Respond with one short sentence."},
                    {"role": "user", "content": "Reply with exactly: OK from <model_name>."},
                ],
            )
            # LLMResponse object — access fields
            content = getattr(resp, "content", "") or getattr(resp, "get", lambda *_: None)("content")
            print(f"  model:   {model_id}")
            print(f"  content: {content[:200] if content else '(empty)'}")
            print(f"  tokens:  {getattr(resp, 'tokens_used', 0)}")
            print(f"  stop:    {getattr(resp, 'stop_reason', None)}")
            return 0
        except Exception as exc:
            msg = str(exc)
            short = msg[:300].replace("\n", " ")
            print(f"  FAIL: {type(exc).__name__}: {short}")
            continue

    print("\nNo candidate model worked.")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

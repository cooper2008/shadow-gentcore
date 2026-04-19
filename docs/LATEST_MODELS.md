# Latest Models — Verified-Working Defaults

This is the canonical reference for which model name to put in your domain's
`config/provider.yaml` for each supported provider. Last verified by
end-to-end test run on **2026-04-19** (see `gentcore-e2e/USER_FLOW_E2E_RESULTS.md`).

When adding a new provider or bumping models, update this file FIRST so it
matches reality, then update the framework defaults in `categories.yaml`,
`anthropic_provider.py`, `bedrock_provider.py`, `cli/ai.py`, and
`server/runner.py` to match.

---

## Quality matrix (E2E genesis + workflow runs)

Empirically observed when running the full genesis pipeline + downstream
workflow on a real codebase reference. **Pick GLM 5.1 first** — it is the
only one that completed end-to-end with excellent code quality.

| Provider | Final workflow status | Code quality | Notes |
|----------|----------------------|--------------|-------|
| **GLM 5.1** | ✓ COMPLETED | excellent | recommended |
| Gemini 3 Flash | ⚠ prose on final (system-prompt hint helps) | good | works with H1 fix |
| MiniMax M2.7 | ⚠ flaky (system-prompt hint helps) | medium | inconsistent across runs |
| Anthropic Claude 4.5 | ✓ COMPLETED (when account funded) | excellent | direct API |

The "hint helps" notes refer to the H1 always-on `submit_output` fix landed
in Phase 2c — without that fix, models that prefer narrative responses
(Gemini, MiniMax) would emit prose instead of structured JSON on final
calls and confuse the downstream output parser.

---

## Recommended defaults

| Provider | Model name (in provider.yaml) | API endpoint | Verified |
|----------|--------------------------------|--------------|----------|
| Anthropic (direct) | `claude-sonnet-4-5-20250929` | `https://api.anthropic.com` | ✓ 2026-04-19 |
| AWS Bedrock | `anthropic.claude-sonnet-4-5-20250929-v1:0` | `bedrock-runtime.<region>.amazonaws.com` | ✓ via SDK |
| GLM (ZHIPU) | `glm-5.1` | `https://open.bigmodel.cn/api/anthropic` | ✓ 2026-04-19 |
| MiniMax | `abab7-chat-preview` (returns model: `MiniMax-M2.7`) | `https://api.minimax.io/anthropic` | ✓ 2026-04-19 |
| Kimi (Moonshot) | `kimi-k2-turbo-preview` | `https://api.moonshot.cn/anthropic` | ⚠ key-dependent |
| OpenAI | `gpt-4.1-mini` | `https://api.openai.com` | ✓ 2026-04-19 (auth + listing only) |

---

## How to wire a non-Anthropic provider via the framework

The framework's `AnthropicProvider` uses the official Anthropic Python SDK,
which honors `ANTHROPIC_BASE_URL` and `ANTHROPIC_API_KEY` env vars. So any
endpoint that exposes an Anthropic-compatible Messages API can be used:

```bash
# In your domain's config/provider.yaml:
provider: anthropic
model: glm-5.1                    # vendor's model name, NOT a Claude name
max_tokens: 8192
api_key_env: ANTHROPIC_API_KEY    # framework reads this env var

# In your shell (CI / dev env):
export ANTHROPIC_API_KEY="$ZHIPU_API_KEY"           # or your vendor's key
export ANTHROPIC_BASE_URL="https://open.bigmodel.cn/api/anthropic"
export ANTHROPIC_AUTH_TOKEN="$ZHIPU_API_KEY"

ai genesis build --domain .
```

Both `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN` need the same value
because (a) the framework's CLI checks `ANTHROPIC_API_KEY` to decide
"yes I have anthropic creds", and (b) the SDK sends `ANTHROPIC_AUTH_TOKEN`
as the actual bearer token. We are tracking a refactor to collapse this
to a single var.

---

## Test that a provider works before genesis

```bash
# Plain text — minimum viability check
curl -sS -X POST "$ANTHROPIC_BASE_URL/v1/messages" \
  -H "Authorization: Bearer $ANTHROPIC_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model":"glm-5.1","max_tokens":20,"messages":[{"role":"user","content":"Reply OK"}]}'

# Tool-use (genesis agents need this)
curl -sS -X POST "$ANTHROPIC_BASE_URL/v1/messages" \
  -H "Authorization: Bearer $ANTHROPIC_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "glm-5.1",
    "max_tokens": 200,
    "messages": [{"role": "user", "content": "Use the file_read tool to read README.md"}],
    "tools": [{"name": "file_read", "description": "Read a file", "input_schema": {"type": "object", "required": ["path"], "properties": {"path": {"type": "string"}}}}]
  }'
```

If both return real `{content: [...]}` payloads, genesis will work. If you
see `{"error": ...}` or an HTML error page, fix that first.

---

## Why this matters

The framework had `claude-sonnet-4-6-20250414` hardcoded as the default in
**5+ files**. That model name does not exist on the Anthropic API. Every
fresh user installation hit a silent HTTP 404 on first genesis run, with
the framework's gate-retry path swallowing the error and surfacing only
`step: error`. This file is the single source of truth so the next bump
(Sonnet 4.6, M3.0, etc.) is a one-line documentation change followed by
a coordinated codebase patch.

# Provider Guide — connecting shadow-gentcore to any LLM backend

> **Note:** This guide covers LLM provider credentials (API keys for Claude, OpenAI, Bedrock, etc.).
> For service tool credentials (Jira, GitHub, Slack tokens), see [CREDENTIALS_GUIDE.md](CREDENTIALS_GUIDE.md).

Every agent calls an LLM. This guide shows you how to route those calls through:

- Anthropic native (Claude Opus / Sonnet / Haiku)
- OpenAI native (GPT-5 family)
- AWS Bedrock (Claude on Bedrock + bearer token)
- Claude Code subscription (`claude -p`, no API key)
- Minimax M2 (Anthropic-compatible gateway)
- GLM / BigModel (Anthropic-compatible gateway)
- Gemini (OpenAI-compatible gateway)
- DeepSeek, Together, OpenRouter (OpenAI-compatible)
- DryRunProvider (zero-cost pipeline smoke)
- SmokeTestProvider (zero-cost, schema-correct stubs)

Every provider implements the same `BaseProvider.chat(messages, **kwargs)` interface, so swapping them is a single-line change.

---

## TL;DR — provider selection matrix

| Use case | Recommended |
|----------|-------------|
| CI / pre-commit smoke | `SmokeTestProvider` |
| Local `./ai ... --dry-run` | `DryRunProvider` (automatic) |
| Production genesis | Claude Sonnet or Opus via `AnthropicProvider` |
| Production feature workflow | Claude Sonnet (best cost/quality for codegen) |
| Cheap Chinese-market gateway | GLM 5.1 via BigModel (Anthropic-compat) — produces excellent code |
| OpenAI-style tool loops | Gemini 3 Flash or GPT-5 |
| No API key available | `ClaudeCodeProvider` (`claude -p` subscription) — prose agents only |

---

## Anthropic native

```python
from harness.providers.anthropic_provider import AnthropicProvider

provider = AnthropicProvider(
    api_key="sk-ant-...",            # or set ANTHROPIC_API_KEY
    model="claude-sonnet-4-5-20250929",
    max_tokens=8192,
)
```

Env-var setup:

```bash
export ANTHROPIC_API_KEY=sk-ant-api03-...
./ai genesis build --team my-backend
```

**Key feature:** H1 forced-`submit_output`. When an agent declares `output_schema`, the provider injects a `submit_output` tool and either forces it (no other tools) or runs it coexist-mode (tools + auto tool_choice), so the model's reply is guaranteed schema-valid JSON.

---

## Anthropic-compatible gateways (Minimax, GLM, …)

Many providers speak the Anthropic Messages API protocol. Point the same `AnthropicProvider` at their endpoint:

### Minimax M2

```bash
export ANTHROPIC_BASE_URL=https://api.minimax.io/anthropic
export ANTHROPIC_AUTH_TOKEN=sk-cp-...
```

```python
provider = AnthropicProvider(
    auth_token="sk-cp-...",
    base_url="https://api.minimax.io/anthropic",
    model="m2.7",
    max_tokens=2048,
)
```

### GLM 5.1 via BigModel.cn

```bash
export ANTHROPIC_BASE_URL=https://open.bigmodel.cn/api/anthropic
export ANTHROPIC_AUTH_TOKEN=...
```

```python
provider = AnthropicProvider(
    auth_token="...",
    base_url="https://open.bigmodel.cn/api/anthropic",
    model="glm-5.1",
    max_tokens=4096,
)
```

Model names known to work on BigModel: `glm-5.1`, `glm-5`, `glm-4.6`, `glm-4.5`, `glm-4.5-x`, `glm-4.5-airx`, `glm-4.5-flash`.

**Per-model nudges** are automatically applied by `harness/providers/model_hints.py`:
- Minimax M2 gets an Alembic + JSON-only reminder
- GLM variants get a light JSON-only nudge
- Claude native models get no nudge (they follow schemas well already)

---

## OpenAI native

```python
from harness.providers.openai_provider import OpenAIProvider

provider = OpenAIProvider(
    api_key="sk-...",             # or set OPENAI_API_KEY
    model="gpt-5.4",
    max_tokens=4096,
)
```

When `output_schema` is declared, the provider adds `response_format={"type": "json_object"}` **and** injects a schema hint into the system prompt.

---

## OpenAI-compatible gateways (Gemini, DeepSeek, Together, …)

Point `OpenAIProvider` at the compatible endpoint:

### Gemini 3 (preview family)

Flagship = `gemini-3.1-pro-preview` (most capable). Flash-tier = `gemini-3-flash-preview`
(pro-level quality at flash speed). Lite = `gemini-3.1-flash-lite-preview` (cost-efficient
high-volume). All are in preview per Google's [Gemini 3 docs](https://ai.google.dev/gemini-api/docs/gemini-3).

```python
provider = OpenAIProvider(
    api_key="AIza...",            # Gemini API key
    base_url="https://generativelanguage.googleapis.com/v1beta/openai",
    model="gemini-3.1-pro-preview",   # flagship; swap for -flash-preview for speed
    max_tokens=4096,
)
```

Env-var alternative:

```bash
export OPENAI_API_KEY=AIza...              # force precedence over any existing OpenAI key
export OPENAI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai
```

**The framework automatically:**
- Translates Anthropic-format tool schemas (`{name, input_schema}`) → OpenAI format (`{type: function, function: {name, parameters}}`)
- Translates assistant `tool_use` blocks → `tool_calls` + `role: tool` messages
- Forces `response_format={"type": "json_object"}` when a schema is declared (and no tools)
- **Preserves Gemini 3 `thought_signature`** across tool-use turns automatically — pro and flash
  thinking variants both round-trip correctly. No adapter work needed by callers.

### DeepSeek

```bash
export OPENAI_API_KEY=sk-...
export OPENAI_BASE_URL=https://api.deepseek.com/v1
```

```python
provider = OpenAIProvider(model="deepseek-chat", base_url="https://api.deepseek.com/v1")
```

---

## AWS Bedrock

```python
from harness.providers.bedrock_provider import BedrockProvider

provider = BedrockProvider(
    model="anthropic.claude-sonnet-4-v1:0",
    region="us-east-1",
    bearer_token="...",           # or uses AWS default credential chain
)
```

---

## Claude Code subscription (`claude -p`)

Routes LLM calls through the local `claude` CLI — uses Max/Pro/Team subscription credits, **not** API tokens.

```python
from harness.providers.claudecode_provider import ClaudeCodeProvider
provider = ClaudeCodeProvider(timeout=180)
```

**Limitation:** `claude -p` is one-shot text I/O with `--max-turns 1`. That means:
- ✅ Works: planner, reviewer, any prose-only agent
- ❌ Does not work: any agent with `file_write` / `shell_exec` / tool calls

For mixed workflows, pair it with an API provider for the tool-using steps.

---

## Zero-cost providers

### `DryRunProvider`

Used automatically when you pass `--dry-run` to any CLI command:

```bash
./ai genesis build --team my-backend --dry-run
./ai run workflow path/to.yaml --task '{...}' --dry-run
```

Produces generic `[DRY RUN] Agent: ...` output. Good for proving the pipeline runs; doesn't produce real output.

### `SmokeTestProvider`

Schema-aware — it reads the agent's `output_schema` from the system prompt and emits a minimal valid JSON object. Perfect for CI.

```bash
./ai test smoke                    # full journey
./ai test smoke --cross-domain     # backend+frontend
./ai test smoke --domain PATH      # health-check an existing domain
```

Under the hood the runner wires up:

```python
from harness.providers.smoke_test_provider import SmokeTestProvider
provider = SmokeTestProvider()
```

---

## How the provider plugs into the runtime

```
AgentRunner
 └── provider.chat(messages, tools=?, output_schema=?)
       │
       ├── AnthropicProvider:
       │     forces submit_output tool when schema declared → guaranteed JSON
       │     supports base_url + auth_token for Anthropic-compat gateways
       │
       ├── OpenAIProvider:
       │     translates Anthropic tool shape ↔ OpenAI tool shape
       │     translates tool_use/tool_result ↔ tool_calls/role=tool history
       │     forces response_format=json_object when schema declared
       │
       ├── BedrockProvider / ClaudeCodeProvider: bespoke
       │
       └── Dry/Smoke providers: no network
```

Both `AnthropicProvider` and `OpenAIProvider` automatically prepend a **model-specific hint** to the system prompt via `harness.providers.model_hints.get_model_hint(model)` — only for models registered as known-to-need-nudging.

---

## Setting per-agent provider (advanced)

Default: the provider comes from your domain's `domain.yaml` → `provider:` block, or from the CLI. You can override per agent-manifest:

```yaml
# agents/MyAgent/v1/agent_manifest.yaml
id: my-domain/MyAgent/v1
provider:
  name: anthropic
  model: claude-opus-4-7        # override: use Opus for this agent only
  max_tokens: 16384
```

This is useful when different steps want different models — e.g. Opus for the architect, Sonnet for the implementer, Haiku for the linter.

---

## Debugging a new provider

1. **Direct smoke** — call `provider.chat(...)` with a simple messages list; print the raw response.
2. **Single agent** — use `./ai run agent <id> --task "..." --domain PATH`. Watch for `provider: X` in the header.
3. **Full workflow** — use one of the sample scripts in `scripts/` as a template:
   - `scripts/minimax_real_agent.py` + `scripts/minimax_real_workflow_writes.py`
   - `scripts/gemini_real_workflow.py`
   - `scripts/glm_real_workflow.py`
   - `scripts/claudecode_planner_test.py`

Each prints a full `execution_log` with gate decisions, feedback-loop triggers, retry attempts, and per-step output signals.

---

## Provider reliability notes (as of Phase 2e)

From live testing on the acme-backend feature_delivery workflow:

| Provider + model | Code quality | Final-JSON reliability |
|------------------|--------------|------------------------|
| Claude Opus 4.7 / Sonnet 4.6 | Excellent | Excellent (submit_output forced) |
| **GLM 5.1** | **Excellent** (keyset pagination, clean separation) | Excellent |
| Gemini 3 Flash | Good | Medium (prose on final turn — nudge helps) |
| Minimax M2.7 | Medium (hallucinated Alembic API on first pass) | Medium (nudge helps) |
| Gemini 3 Pro (thinking) | Unknown | Blocked — needs thought_signature support |

**Recommendation for teams shipping to production:** use Claude Sonnet or GLM 5.1 for the genesis + implement/test/review steps. Budget: ~$1-3 per feature with Sonnet, ~$0.30-1 with GLM.

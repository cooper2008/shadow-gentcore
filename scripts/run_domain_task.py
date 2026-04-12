#!/usr/bin/env python3
"""Run generated domain agents on a real task.

Loads each agent's system_prompt.md and context/standards.md from domain-fullstack/,
then runs them via claude -p (subscription auth) with the task.
This tests the DOMAIN AGENTS themselves — their prompts, standards, and context.

Workflow: BackendAgent → FrontendAgent → ReviewerAgent

Usage:
    .venv/bin/python scripts/run_domain_task.py
    .venv/bin/python scripts/run_domain_task.py --task "Add X feature"
    .venv/bin/python scripts/run_domain_task.py --agent BackendAgent
"""

from __future__ import annotations

import argparse
import asyncio
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOMAIN_DIR = ROOT / "domain-fullstack"
SAMPLE_PROJECT = ROOT / "sample_project"
PROJECT_OUTPUT = ROOT / "project-output"
STEPS_DIR = ROOT / ".domain_steps"

DEFAULT_TASK = (
    "Add a POST /v1/payments/{id}/refund endpoint. "
    "It must: validate the payment exists, check its status is 'completed', "
    "transition it to 'refunded', persist the change, and return the updated payment. "
    "Include integration tests: success, payment-not-found (404), invalid-status (422)."
)


# ── Helpers ─────────────────────────────────────────────────────────────────

def log(msg: str) -> None:
    print(msg, flush=True)


def trunc(s: str, n: int) -> str:
    return s[:n] if len(s) > n else s


def load_agent(name: str) -> tuple[str, str, str]:
    """Load agent system_prompt, standards, and glossary from domain-fullstack/."""
    agent_dir = DOMAIN_DIR / "agents" / name / "v1"
    system_prompt = (agent_dir / "system_prompt.md").read_text()
    standards = (DOMAIN_DIR / "context" / "standards.md").read_text()
    glossary = (DOMAIN_DIR / "context" / "glossary.md").read_text()
    return system_prompt, standards, glossary


async def run_claude(prompt: str, name: str, timeout: int = 900, max_turns: int = 30) -> str:
    """Run claude -p with the prompt and return output. Prints progress."""
    log(f"  [{name}] Sending to claude-p ({len(prompt):,} chars, max-turns={max_turns})...")

    cmd = ["claude", "-p", "--dangerously-skip-permissions", "--max-turns", str(max_turns)]
    import os
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}

    if len(prompt) > 50000:
        prompt = prompt[:50000] + "\n\n[Context truncated. Produce your final output now.]"

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        start = time.time()
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=prompt.encode("utf-8")), timeout=timeout
        )
        elapsed = time.time() - start
        out = stdout.decode("utf-8", errors="replace").strip()
        if proc.returncode != 0 and not out:
            err = stderr.decode("utf-8", errors="replace").strip()
            out = f"Error: {err}" if err else f"Error: exit code {proc.returncode}"
        status = "ERROR" if out.startswith("Error:") else "OK"
        log(f"  [{name}] {status} in {elapsed:.1f}s — {len(out):,} chars output")
        return out
    except asyncio.TimeoutError:
        log(f"  [{name}] TIMEOUT after {timeout}s")
        return f"Error: Timeout after {timeout}s"
    except Exception as e:
        log(f"  [{name}] FAILED: {e}")
        return f"Error: {e}"


def extract_files(content: str, base_dir: Path) -> list[str]:
    """Parse agent output for embedded file contents and write them to disk."""
    written: list[str] = []
    patterns = [
        re.compile(r"###\s+FILE:\s*([^\n]+)\n```[^\n]*\n(.*?)```", re.DOTALL),
        re.compile(r"\*\*([\w/.-]+\.(?:py|ts|tsx|yaml|yml|json|md|tf|sql))\*\*\n```[^\n]*\n(.*?)```", re.DOTALL),
        re.compile(r"`([\w/.-]+\.(?:py|ts|tsx|yaml|yml|json|md|tf|sql))`:\n```[^\n]*\n(.*?)```", re.DOTALL),
        re.compile(r"#+\s+([\w/.-]+\.(?:py|ts|tsx|yaml|yml|json|md|tf|sql))\n```[^\n]*\n(.*?)```", re.DOTALL),
    ]
    seen: set[str] = set()
    for pattern in patterns:
        for m in pattern.finditer(content):
            rel = m.group(1).strip().lstrip("/")
            if rel in seen or "<" in rel or " " in rel:
                continue
            seen.add(rel)
            body = m.group(2)
            full = base_dir / rel
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(body)
            written.append(rel)
            log(f"    [write] {rel}  ({len(body):,} bytes)")
    return written


def print_step(num: int, name: str, result: str, elapsed: float, files: list[str]) -> None:
    status = "ERROR" if result.startswith("Error:") else "OK"
    log(f"\n{'─'*70}")
    log(f"STEP {num}: {name}  [{status}]  {elapsed:.1f}s  {len(result):,} chars")
    if files:
        log(f"  Files written: {len(files)}")
        for f in files[:8]:
            log(f"    {f}")
    preview = result[:400].replace("\n", "\n  ")
    log(f"  Preview:\n  {preview}...")
    log(f"{'─'*70}")


# ── Prompt builders (inject real agent system prompts from domain-fullstack/) ─

def build_backend_prompt(task: str, system_prompt: str, standards: str) -> str:
    return f"""\
{system_prompt}

---
## Domain Standards (from domain-fullstack/context/standards.md)
{trunc(standards, 3000)}

---
## Source Code
The existing backend is at: {SAMPLE_PROJECT}/backend

Use your Read and Write tools to:
1. Read the existing files to understand the current structure:
   - {SAMPLE_PROJECT}/backend/main.py
   - {SAMPLE_PROJECT}/backend/models/payment.py
   - {SAMPLE_PROJECT}/backend/routes/payments.py
   - {SAMPLE_PROJECT}/backend/schemas/payment.py
2. Generate the new/updated code
3. Write output files to: {PROJECT_OUTPUT}/backend/

---
## Task
{task}

## Required Output Files
Write these to {PROJECT_OUTPUT}/backend/:
- routes/payments.py  (add the refund endpoint)
- schemas/payment.py  (add RefundRequest schema if needed)
- tests/test_refund.py  (integration tests)

After writing, list {PROJECT_OUTPUT}/backend/ to confirm the files exist.
"""


def build_frontend_prompt(task: str, system_prompt: str, standards: str, backend_summary: str) -> str:
    return f"""\
{system_prompt}

---
## Domain Standards
{trunc(standards, 2000)}

---
## Backend Changes (just completed by BackendAgent)
{trunc(backend_summary, 2000)}

---
## Source Code
The existing frontend is at: {SAMPLE_PROJECT}/frontend

Read key frontend files before generating code.

---
## Task
{task}

The backend now has POST /v1/payments/{{id}}/refund.
Add frontend support: a RefundButton component and useRefund hook.

Write to {PROJECT_OUTPUT}/frontend/:
- components/RefundButton.tsx
- hooks/useRefund.ts

TypeScript strict mode. Named exports only. No `any` type.
"""


def build_reviewer_prompt(
    task: str,
    system_prompt: str,
    standards: str,
    backend_output: str,
    frontend_output: str,
) -> str:
    return f"""\
{system_prompt}

---
## Domain Standards
{trunc(standards, 2000)}

---
## Task Being Reviewed
{task}

## BackendAgent Output
{trunc(backend_output, 2500)}

## FrontendAgent Output
{trunc(frontend_output, 2000)}

---
## Review Checklist
For each item, give verdict: PASS / FAIL / WARN

1. Standards compliance: type hints, async/await, structlog, no bare print
2. API correctness: HTTP status codes, error handling, response schema
3. Status lifecycle: only completed→refunded transition allowed (422 otherwise)
4. Test coverage: success + payment-not-found (404) + wrong-status (422)
5. Frontend: TypeScript strict, named exports, error handling
6. Cross-domain: frontend calls correct endpoint URL

Output: overall score (0-100), per-item verdicts, required fixes, approved: yes/no
"""


# ── Main ─────────────────────────────────────────────────────────────────────

async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", default=DEFAULT_TASK)
    parser.add_argument(
        "--agent",
        choices=["BackendAgent", "FrontendAgent", "ReviewerAgent", "all"],
        default="all",
    )
    args = parser.parse_args()

    STEPS_DIR.mkdir(exist_ok=True)
    PROJECT_OUTPUT.mkdir(exist_ok=True)

    log("=" * 70)
    log("DOMAIN AGENT WORKFLOW")
    log(f"  Domain:  {DOMAIN_DIR}")
    log(f"  Source:  {SAMPLE_PROJECT}")
    log(f"  Output:  {PROJECT_OUTPUT}")
    log(f"  Task:    {args.task[:100]}...")
    log("  Agents:  BackendAgent → FrontendAgent → ReviewerAgent")
    log("  Prompts: loaded from domain-fullstack/agents/*/v1/system_prompt.md")
    log("  Context: loaded from domain-fullstack/context/standards.md")
    log("=" * 70)

    start_total = time.time()
    only = args.agent

    # ── Step 1: BackendAgent ───────────────────────────────────────────────
    log("\n--- STEP 1: BackendAgent ---")
    log("  Loading: domain-fullstack/agents/BackendAgent/v1/system_prompt.md")
    be_sys, standards, glossary = load_agent("BackendAgent")
    _ = glossary

    t1 = time.time()
    r_backend = await run_claude(
        build_backend_prompt(args.task, be_sys, standards),
        "BackendAgent",
        timeout=900,
        max_turns=30,
    )
    if r_backend.startswith("Error:") and only != "BackendAgent":
        log("  [RETRY] BackendAgent failed — retrying once...")
        r_backend = await run_claude(
            build_backend_prompt(args.task, be_sys, standards),
            "BackendAgent",
            timeout=900,
            max_turns=30,
        )
    (STEPS_DIR / "step1_backend.txt").write_text(r_backend)
    be_files = extract_files(r_backend, PROJECT_OUTPUT / "backend")
    print_step(1, "BackendAgent", r_backend, time.time() - t1, be_files)

    if only == "BackendAgent":
        log("\nSingle-agent mode: done.")
        return

    # ── Step 2: FrontendAgent ──────────────────────────────────────────────
    log("\n--- STEP 2: FrontendAgent ---")
    log("  Loading: domain-fullstack/agents/FrontendAgent/v1/system_prompt.md")
    fe_sys, fe_standards, _ = load_agent("FrontendAgent")

    t2 = time.time()
    r_frontend = await run_claude(
        build_frontend_prompt(args.task, fe_sys, fe_standards, trunc(r_backend, 3000)),
        "FrontendAgent",
        timeout=600,
        max_turns=20,
    )
    (STEPS_DIR / "step2_frontend.txt").write_text(r_frontend)
    fe_files = extract_files(r_frontend, PROJECT_OUTPUT / "frontend")
    print_step(2, "FrontendAgent", r_frontend, time.time() - t2, fe_files)

    if only == "FrontendAgent":
        log("\nSingle-agent mode: done.")
        return

    # ── Step 3: ReviewerAgent ──────────────────────────────────────────────
    log("\n--- STEP 3: ReviewerAgent ---")
    log("  Loading: domain-fullstack/agents/ReviewerAgent/v1/system_prompt.md")
    rv_sys, rv_standards, _ = load_agent("ReviewerAgent")

    t3 = time.time()
    r_review = await run_claude(
        build_reviewer_prompt(args.task, rv_sys, rv_standards, trunc(r_backend, 2500), trunc(r_frontend, 2000)),
        "ReviewerAgent",
        timeout=600,
        max_turns=15,
    )
    (STEPS_DIR / "step3_review.txt").write_text(r_review)
    print_step(3, "ReviewerAgent", r_review, time.time() - t3, [])

    # ── Summary ────────────────────────────────────────────────────────────
    total = time.time() - start_total
    all_files = sorted(f for f in PROJECT_OUTPUT.rglob("*") if f.is_file())

    log(f"\n{'='*70}")
    log("DOMAIN WORKFLOW COMPLETE")
    log(f"{'='*70}")
    log(f"  Total time:    {total:.1f}s")
    log(f"  Project files: {len(all_files)}")
    for f in all_files:
        log(f"    {f.relative_to(PROJECT_OUTPUT)}  ({f.stat().st_size:,} bytes)")
    log(f"{'='*70}")


if __name__ == "__main__":
    asyncio.run(main())

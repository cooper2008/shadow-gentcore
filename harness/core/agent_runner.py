"""AgentRunner — full agent execution pipeline from manifest to result."""

from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import time
from enum import Enum
from typing import Any

_HOOK_TIMEOUT = 5  # seconds

_hook_logger = logging.getLogger(__name__)


# H3 — FRAMEWORK_AUDIT_2026Q2
# When this env var is truthy (value in {"1", "true", "yes", "on"} — case-insensitive)
# AgentRunner.run() populates ToolExecutor.set_rule_context() with a RuleContext
# built from the agent manifest. This activates RuleEngine Layers 2–6 (category
# overrides, domain policy, agent permissions, workflow/runtime overrides) which
# were dead in production before H3.
_ENFORCE_RULES_ENV = "GENTCORE_ENFORCE_RULES"
_TRUTHY = {"1", "true", "yes", "on"}


def _enforce_rules_enabled() -> bool:
    return os.environ.get(_ENFORCE_RULES_ENV, "").strip().lower() in _TRUTHY


def build_rule_context_from_manifest(manifest: Any) -> "RuleContext":  # noqa: F821 (forward ref)
    """Build a RuleContext from an agent manifest.

    Extracts ``category`` and ``permissions`` (the fields RuleEngine's Layers 2
    and 4 consume). Domain policy, workflow overrides, and trusted_paths are
    left empty for the caller to populate from workspace configuration when
    available.

    Accepts both dict manifests (YAML-loaded) and AgentManifest Pydantic objects.
    """
    from harness.core.rule_engine import RuleContext  # local import to avoid cycles

    if isinstance(manifest, dict):
        category = str(manifest.get("category", ""))
        perms_obj = manifest.get("permissions") or {}
        agent_perms = dict(perms_obj) if isinstance(perms_obj, dict) else {}
    else:
        category = str(getattr(manifest, "category", "") or "")
        perms_obj = getattr(manifest, "permissions", None)
        if perms_obj is None:
            agent_perms = {}
        elif hasattr(perms_obj, "model_dump"):
            agent_perms = perms_obj.model_dump()
        elif isinstance(perms_obj, dict):
            agent_perms = dict(perms_obj)
        else:
            agent_perms = {}

    return RuleContext(
        agent_category=category,
        agent_permissions=agent_perms,
    )


def _call_hook_safe(hook_fn: Any, *args: Any) -> Any:
    """Call a hook function with timeout and exception safety.

    Ensures: (1) hooks cannot hang the agent beyond _HOOK_TIMEOUT seconds,
    (2) hook exceptions do not crash the agent runner, (3) on failure the
    last positional arg (the unmodified input) is returned as a safe fallback.
    """
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(hook_fn, *args)
            try:
                return future.result(timeout=_HOOK_TIMEOUT)
            except concurrent.futures.TimeoutError:
                _hook_logger.error(
                    "Hook %s timed out after %ds", hook_fn.__name__, _HOOK_TIMEOUT,
                )
                return args[-1]  # Return the last arg (usually the unmodified input)
    except Exception as exc:
        _hook_logger.error(
            "Hook %s raised %s: %s", hook_fn.__name__, type(exc).__name__, exc,
        )
        return args[-1]  # Return unmodified input on error

from agent_contracts.manifests.agent_manifest import AgentManifest
from agent_contracts.contracts.task_envelope import TaskEnvelope
from agent_contracts.contracts.run_record import RunRecord, RunStatus

from harness.core.prompt_assembler import PromptAssembler
from harness.core.mode_dispatcher import ModeDispatcher
from harness.core.tool_executor import ToolExecutor
from harness.core.budget_tracker import BudgetTracker, BudgetExceededError
from harness.core.output_parser import OutputParser


class AgentState(str, Enum):
    """Agent lifecycle states — inspired by claw-code's worker state machine.

    Provides observability into where an agent is in its execution pipeline.
    """
    SPAWNING = "spawning"
    READY = "ready"
    RUNNING = "running"
    TOOL_CALLING = "tool_calling"
    VALIDATING = "validating"
    COMPLETED = "completed"
    FAILED = "failed"


class AgentRunner:
    """Orchestrates a single agent execution from manifest loading to result.

    Pipeline:
    1. Load manifest and resolve system prompt
    2. Assemble prompt via PromptAssembler
    3. Select execution strategy via ModeDispatcher
    4. Execute strategy with budget tracking
    5. (Optional) Reflexion: grade output, re-run with critique if below threshold
    6. Return result and RunRecord
    """

    def __init__(
        self,
        provider: Any,
        prompt_assembler: PromptAssembler | None = None,
        mode_dispatcher: ModeDispatcher | None = None,
        tool_executor: ToolExecutor | None = None,
        grading_engine: Any | None = None,
        memory_store: Any | None = None,
    ) -> None:
        self.provider = provider
        self.prompt_assembler = prompt_assembler or PromptAssembler()
        self.mode_dispatcher = mode_dispatcher or ModeDispatcher()
        self.tool_executor = tool_executor
        self.grading_engine = grading_engine
        self.memory_store = memory_store
        self._last_state_log: list[dict[str, Any]] = []

    @property
    def state_log(self) -> list[dict[str, Any]]:
        """Return the state transition log for the last run."""
        return list(self._last_state_log)

    @staticmethod
    def _get(obj: Any, key: str, default: Any = None) -> Any:
        """Get a value from either a Pydantic model or a dict."""
        if hasattr(obj, key):
            return getattr(obj, key)
        if isinstance(obj, dict):
            return obj.get(key, default)
        return default

    async def run(
        self,
        manifest: Any,
        task: Any = None,
        system_prompt_content: str = "",
        tool_descriptions: list[dict[str, Any]] | None = None,
        context_items: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Execute an agent for a given task.

        Args:
            manifest: Agent manifest (AgentManifest or dict).
            task: Task envelope (TaskEnvelope or dict).
            system_prompt_content: Resolved system prompt text.
            tool_descriptions: Resolved tool descriptions for prompt assembly.
            context_items: Additional context items.

        Returns:
            Dict with 'result', 'run_record', 'budget_summary' keys.
        """
        task = task or {}
        state_log: list[dict[str, Any]] = []

        def _set_state(state: AgentState, agent_id: str = "", detail: str = "") -> None:
            state_log.append({
                "state": state.value,
                "agent_id": agent_id,
                "detail": detail,
                "timestamp": time.time(),
            })

        # Extract hooks stored by ManifestLoader (private key, safe for dict manifests only)
        hooks = self._get(manifest, "_hooks", {}) if isinstance(manifest, dict) else {}

        agent_id = self._get(manifest, "id", "unknown")
        _set_state(AgentState.SPAWNING, agent_id)
        start_time = time.monotonic()
        task_id = self._get(task, "task_id", "unknown")
        trace_id = f"trace-{task_id}-{int(time.time())}"

        # Budget setup. Resolution order:
        #   1. task["budget_tokens"] — workflow step override (highest)
        #   2. manifest.execution_mode.budget_tokens — per-agent manifest knob
        #   3. 200000 default — enough for GLM/MiniMax react loops over
        #      large source trees. The previous 100K cap caused
        #      SourceScannerAgent to fail mid-scan with ~115K used.
        manifest_budget = 0
        if isinstance(manifest, dict):
            exec_mode = manifest.get("execution_mode") or {}
            if isinstance(exec_mode, dict):
                try:
                    manifest_budget = int(exec_mode.get("budget_tokens") or 0)
                except (TypeError, ValueError):
                    manifest_budget = 0
        budget = BudgetTracker(
            max_tokens=self._get(task, "budget_tokens", manifest_budget or 200000),
            max_cost_usd=self._get(task, "budget_cost_usd", 2.0),
        )

        # Extract output_schema and declared tool list from manifest.
        # Pass full dicts (preserving desc/level fields) so ToolDisclosureRouter
        # can partition L1 vs L2 tools.  String entries default to L2.
        output_schema: dict[str, Any] | None = None
        declared_tools: list[Any] = []
        if isinstance(manifest, dict):
            output_schema = manifest.get("output_schema")
            declared_tools = list(manifest.get("tools", []))
        else:
            # Pydantic AgentManifest — extract tool names from tool_bindings
            tool_bindings = getattr(manifest, "tool_bindings", None) or []
            declared_tools = [
                {"name": tb.name} if hasattr(tb, "name") else str(tb)
                for tb in tool_bindings
            ]

        # Call pre_execute hook if present — may modify context_items
        if "pre_execute" in hooks:
            context_items = _call_hook_safe(hooks["pre_execute"], manifest, task, context_items or [])

        # Inject past agent memories as context (optional — no-op when memory_store is None)
        if self.memory_store:
            memories = self.memory_store.recall(agent_id, key="run_output", k=3)
            if memories:
                memory_text = "\n".join(f"- {m['value'][:500]}" for m in memories)
                context_items = list(context_items or []) + [
                    {"source": "agent_memory", "content": f"Past outputs from this agent:\n{memory_text}"}
                ]

        # Assemble prompt
        messages = self.prompt_assembler.assemble(
            manifest=manifest,
            system_prompt_content=system_prompt_content,
            tool_descriptions=tool_descriptions,
            context_items=context_items,
            task_input=self._get(task, "input_payload", task if isinstance(task, dict) else {}),
            output_schema=output_schema,
        )

        # Select execution strategy
        execution_mode = self._get(task, "execution_mode_override")
        manifest_em = self._get(manifest, "execution_mode")
        if execution_mode is None and manifest_em:
            execution_mode = manifest_em.model_dump() if hasattr(manifest_em, "model_dump") else manifest_em
        strategy = self.mode_dispatcher.dispatch(execution_mode)
        _set_state(AgentState.READY, agent_id)

        # H3: wire rule context onto the tool executor so RuleEngine Layers 2-6
        # enforce against this agent's declared category + permissions. Gated by
        # GENTCORE_ENFORCE_RULES=1 so pre-H3 behaviour remains the default until
        # domain owners migrate their manifests. Cleared in the `finally` below
        # so one agent's context doesn't leak into another sharing the executor.
        _rule_enforcement_active = (
            _enforce_rules_enabled()
            and self.tool_executor is not None
            and hasattr(self.tool_executor, "set_rule_context")
        )
        if _rule_enforcement_active:
            self.tool_executor.set_rule_context(
                build_rule_context_from_manifest(manifest)
            )

        # G6 — targeted-files allowlist. When the task carries a
        # `targeted_files` list (surfaced by an upstream QualityGate for a
        # Builder retry), restrict file_write/file_edit to those paths so
        # the retry only rewrites the files the gate flagged. Cleared in
        # finally so one retry's allowlist doesn't leak into later steps.
        _targeted_files_active = False
        if self.tool_executor is not None and hasattr(self.tool_executor, "set_targeted_files"):
            _task_input = self._get(task, "input_payload", task if isinstance(task, dict) else {})
            _targeted = _task_input.get("targeted_files") if isinstance(_task_input, dict) else None
            if _targeted:
                self.tool_executor.set_targeted_files(list(_targeted))
                _targeted_files_active = True

        # Execute
        try:
            _set_state(AgentState.RUNNING, agent_id)
            execute_kwargs: dict[str, Any] = {}
            if output_schema:
                execute_kwargs["output_schema"] = output_schema
            if declared_tools:
                execute_kwargs["declared_tools"] = declared_tools
            result = await strategy.execute(
                messages=messages,
                provider=self.provider,
                tool_executor=self.tool_executor,
                **execute_kwargs,
            )

            tokens_used = result.get("tokens_used", 0)
            budget.record_usage(tokens=tokens_used)

            # Post-process: extract structured JSON from content
            raw_content = result.get("content", "")
            parse_log: dict[str, Any] = {"attempted": False}
            if output_schema and raw_content:
                parse_log["attempted"] = True
                parsed = OutputParser().parse(raw_content, output_schema)
                if parsed is not None:
                    result["parsed_output"] = parsed
                    parse_log["success"] = True
                    parse_log["strategy"] = "output_parser"
                else:
                    parse_log["success"] = False
            result["output_parse_log"] = parse_log

            # Call post_execute hook if present — may transform result
            if "post_execute" in hooks:
                result = _call_hook_safe(hooks["post_execute"], manifest, task, result)

            # Framework-level file persistence: when manifest.persist_files=True
            # and the agent emitted a `files: [{path, content}]` array in its
            # parsed output, write each file to disk under the task's workspace.
            # This closes the end-to-end loop for code-writing domain agents
            # (CodeWriter, MigrationAgent, etc.) — without it their structured
            # output dies in Tier 4 memory and never reaches the user's
            # filesystem. Genesis Builder's hooks.py runs first and replaces
            # `files` with `files_created` on success, so it short-circuits
            # cleanly here.
            if isinstance(result, dict):
                self._persist_output_files(manifest, task, result)

            _set_state(AgentState.VALIDATING, agent_id)

            # Persist result summary to long-term memory (optional)
            if self.memory_store and isinstance(result, dict):
                output = result.get("content") or result.get("output") or ""
                if output and len(str(output)) > 10:
                    self.memory_store.store(
                        agent_id=agent_id,
                        key="run_output",
                        value=str(output)[:2000],
                        metadata={"task_id": task_id, "status": "completed"},
                    )

            duration_ms = int((time.monotonic() - start_time) * 1000)

            run_record = RunRecord(
                trace_id=trace_id,
                task_id=self._get(task, "task_id", "unknown"),
                workflow_id=self._get(task, "workflow_id"),
                agent_id=self._get(task, "agent_id", self._get(manifest, "id", "unknown")),
                agent_version=self._get(manifest, "version", "1.0.0"),
                provider="unknown",
                model="unknown",
                tokens_used=tokens_used,
                duration_ms=duration_ms,
                status=RunStatus.SUCCESS,
            )

            # Surface provider/API errors that were caught and stored in result
            result_error = result.get("error") if isinstance(result, dict) else None
            if result_error:
                _set_state(AgentState.FAILED, agent_id, result_error)
                self._last_state_log = state_log
                return {
                    "result": result,
                    "run_record": run_record,
                    "budget_summary": budget.summary(),
                    "state_log": list(state_log),
                    "status": "error",
                    "error": result_error,
                    "output": "",
                    "content": "",
                }

            _set_state(AgentState.COMPLETED, agent_id)
            self._last_state_log = state_log

            content_str = result.get("content", "") if isinstance(result, dict) else str(result)

            # Promote schema fields to top-level so OutputValidator + workflow
            # gates can read them. Prefer result["parsed_output"] (set by
            # OutputParser's 4-strategy extraction above — handles prose+JSON,
            # markdown fences, multi-block outputs). Fall back to raw json.loads
            # of content for the pure submit_output path.
            promoted: dict[str, Any] = {}
            if isinstance(result, dict) and isinstance(result.get("parsed_output"), dict):
                promoted = result["parsed_output"]
            elif content_str:
                try:
                    parsed = json.loads(content_str)
                    if isinstance(parsed, dict):
                        promoted = parsed
                except (json.JSONDecodeError, TypeError):
                    pass

            # Warn loudly when an agent "completes" with nothing usable —
            # this almost always means the Anthropic-compat vendor returned
            # prose instead of a submit_output tool_use, or the response
            # was truncated mid-JSON. Without this log the operator only
            # sees a downstream gate-fail with no hint at the root cause.
            _log = logging.getLogger(__name__)
            if not promoted and not (content_str or "").strip():
                _log.warning(
                    "Agent %s completed with empty output (no parsed_output, "
                    "no content). Likely causes: provider didn't call submit_output, "
                    "response truncated, or schema mismatch. tool_calls=%s",
                    agent_id,
                    [tc.get("name") if isinstance(tc, dict) else str(tc)
                     for tc in (result.get("tool_calls", []) if isinstance(result, dict) else [])][:5],
                )
            elif not promoted and content_str:
                _log.warning(
                    "Agent %s completed with raw content but no structured "
                    "output (JSON parse failed). First 300 chars: %s",
                    agent_id, content_str[:300],
                )

            wrapper: dict[str, Any] = {
                "result": result,
                "run_record": run_record,
                "budget_summary": budget.summary(),
                "state_log": list(state_log),
                "status": "completed",
                "output": promoted if promoted else content_str,
                "content": content_str,
            }
            # Promote schema fields without overwriting framework keys.
            _reserved = set(wrapper.keys()) | {"error"}
            for key, value in promoted.items():
                if key not in _reserved:
                    wrapper[key] = value

            # Tier-citation scoring (opt-in per agent). Manifest can declare:
            #   citations:
            #     min: 1            # at least N citations required
            #     require_tiers: [T2, T3]   # these tiers must be cited
            # When `citations:` is absent, scoring still runs but is neutral
            # (score=1.0 when no citations emitted) — lets gate expressions
            # use `citation_score` opportunistically without breaking agents
            # that don't emit citations yet.
            try:
                from harness.core.citation_checker import score_citations
                cit_cfg = self._get(manifest, "citations") or {}
                min_c = int(cit_cfg.get("min", 0)) if isinstance(cit_cfg, dict) else 0
                req_tiers = cit_cfg.get("require_tiers") if isinstance(cit_cfg, dict) else None
                cit_report = score_citations(
                    promoted if promoted else {},
                    min_citations=min_c,
                    require_tiers=req_tiers if isinstance(req_tiers, list) else None,
                )
                wrapper["_citation_report"] = {
                    "score": cit_report.score,
                    "passed": cit_report.passed,
                    "total_claims": cit_report.total_claims,
                    "cited_claims": cit_report.cited_claims,
                    "findings": [
                        {"severity": f.severity, "message": f.message, "claim": f.claim}
                        for f in cit_report.findings
                    ],
                }
                # Also surface scalars at top-level so gate expressions can
                # use them without walking the dotpath each time:
                #   condition: "status == completed and citation_score >= 0.75"
                wrapper["citation_score"] = cit_report.score
                wrapper["citation_passed"] = cit_report.passed
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "citation scoring failed for %s: %s", agent_id, exc,
                )

            return wrapper

        except BudgetExceededError as exc:
            _set_state(AgentState.FAILED, agent_id, str(exc))
            self._last_state_log = state_log
            duration_ms = int((time.monotonic() - start_time) * 1000)
            run_record = RunRecord(
                trace_id=trace_id,
                task_id=self._get(task, "task_id", "unknown"),
                workflow_id=self._get(task, "workflow_id"),
                agent_id=self._get(task, "agent_id", self._get(manifest, "id", "unknown")),
                agent_version=self._get(manifest, "version", "1.0.0"),
                provider="unknown",
                model="unknown",
                tokens_used=budget.tokens_used,
                duration_ms=duration_ms,
                status=RunStatus.FAILURE,
                failure_reason=str(exc),
            )
            return {
                "result": {"content": "", "error": str(exc)},
                "run_record": run_record,
                "budget_summary": budget.summary(),
                "status": "error",
                "error": str(exc),
                "output": "",
                "content": "",
            }

        except Exception as exc:
            _set_state(AgentState.FAILED, agent_id, str(exc))
            self._last_state_log = state_log
            duration_ms = int((time.monotonic() - start_time) * 1000)
            run_record = RunRecord(
                trace_id=trace_id,
                task_id=self._get(task, "task_id", "unknown"),
                workflow_id=self._get(task, "workflow_id"),
                agent_id=self._get(task, "agent_id", self._get(manifest, "id", "unknown")),
                agent_version=self._get(manifest, "version", "1.0.0"),
                provider="unknown",
                model="unknown",
                tokens_used=budget.tokens_used,
                duration_ms=duration_ms,
                status=RunStatus.FAILURE,
                failure_reason=str(exc),
            )
            return {
                "result": {"content": "", "error": str(exc)},
                "run_record": run_record,
                "budget_summary": budget.summary(),
                "status": "error",
                "error": str(exc),
                "output": "",
                "content": "",
            }

        finally:
            # H3: always clear the rule context so one agent's permissions
            # don't leak into the next agent sharing the same ToolExecutor.
            if _rule_enforcement_active and self.tool_executor is not None:
                self.tool_executor.set_rule_context(None)
            # G6: always clear the targeted-files allowlist so one retry's
            # file scope doesn't carry over to the next agent.
            if _targeted_files_active and self.tool_executor is not None:
                self.tool_executor.set_targeted_files(None)

    async def run_with_reflexion(
        self,
        manifest: AgentManifest,
        task: TaskEnvelope,
        system_prompt_content: str,
        contract: Any = None,
        tool_descriptions: list[dict[str, Any]] | None = None,
        context_items: list[dict[str, str]] | None = None,
        max_reflexion_rounds: int = 2,
        score_threshold: float = 1.0,
    ) -> dict[str, Any]:
        """Execute agent with self-critique / reflexion loop.

        After each execution, grades output via GradingEngine. If score is
        below threshold, re-runs with critique feedback injected into context.

        Args:
            manifest: Agent manifest.
            task: Task envelope.
            system_prompt_content: System prompt text.
            contract: FeatureContract for grading (optional).
            tool_descriptions: Tool descriptions.
            context_items: Context items.
            max_reflexion_rounds: Max re-run attempts.
            score_threshold: Score needed to pass without re-run.

        Returns:
            Dict with 'result', 'run_record', 'reflexion_history', 'rounds'.
        """
        reflexion_history: list[dict[str, Any]] = []
        current_context = list(context_items or [])
        last_result: dict[str, Any] = {}

        for round_num in range(1, max_reflexion_rounds + 1):
            last_result = await self.run(
                manifest=manifest,
                task=task,
                system_prompt_content=system_prompt_content,
                tool_descriptions=tool_descriptions,
                context_items=current_context,
            )

            reflexion_entry: dict[str, Any] = {
                "round": round_num,
                "result": last_result["result"],
            }

            # Grade if grading_engine and contract are available
            if self.grading_engine is not None and contract is not None:
                graded = await self.grading_engine.grade(
                    contract, last_result["result"], self.provider,
                )
                score = graded.score() if hasattr(graded, "score") else 0.0
                reflexion_entry["score"] = score
                reflexion_entry["graded"] = True

                if score >= score_threshold:
                    reflexion_entry["action"] = "pass"
                    reflexion_history.append(reflexion_entry)
                    break

                # Inject critique as context for next round
                manifest_schema: dict[str, Any] | None = None
                if isinstance(manifest, dict):
                    manifest_schema = manifest.get("output_schema")
                critique = self._build_critique(graded, output_schema=manifest_schema)
                current_context = list(context_items or []) + [
                    {"source": "reflexion_critique", "content": critique},
                ]
                reflexion_entry["action"] = "retry_with_critique"
                reflexion_entry["critique"] = critique
            else:
                reflexion_entry["graded"] = False
                reflexion_entry["action"] = "no_grading"
                reflexion_history.append(reflexion_entry)
                break

            reflexion_history.append(reflexion_entry)

        last_result["reflexion_history"] = reflexion_history
        last_result["rounds"] = len(reflexion_history)
        return last_result

    def _persist_output_files(self, manifest: Any, task: Any, result: dict[str, Any]) -> None:
        """Write any `files: [{path, content}]` array in the agent's output to disk.

        Activated when ``manifest.persist_files`` is True. Looks at
        ``result["parsed_output"]`` and ``result["output"]`` (in that order)
        for a list under the ``files`` key. Each entry is written to
        ``task.workspace_root`` (or ``task.output_dir``, or task.domain_path
        as fallback). Non-absolute paths are joined; absolute paths inside
        the workspace pass through; absolute paths outside the workspace
        are rejected to keep the writer scope-guarded.

        Failures are caught and recorded in
        ``result["files_persisted"] = {"written": [...], "failed": [...]}``
        so the workflow gate can see what landed on disk and what didn't.
        Never raises — the run continues even if persistence fails.
        """
        # Gate on manifest flag
        persist = self._get(manifest, "persist_files", False)
        if not persist:
            return

        # Find the files array — pydantic dump puts it on parsed_output
        files: Any = None
        for key in ("parsed_output", "output"):
            container = result.get(key)
            if isinstance(container, dict):
                candidate = container.get("files")
                if isinstance(candidate, list) and candidate:
                    files = candidate
                    break
        if not files:
            return

        # Resolve target dir
        from pathlib import Path
        target_raw: Any = (
            self._get(task, "workspace_root")
            or self._get(task, "output_dir")
            or self._get(task, "domain_path")
        )
        if not target_raw:
            ip = self._get(task, "input_payload")
            if isinstance(ip, dict):
                target_raw = ip.get("workspace_root") or ip.get("output_dir") or ip.get("domain_path")
        if not target_raw:
            result.setdefault("files_persisted", {}).setdefault("failed", []).append(
                {"reason": "no workspace_root/output_dir/domain_path on task"}
            )
            return

        try:
            target_root = Path(str(target_raw)).expanduser().resolve()
        except Exception as exc:
            result.setdefault("files_persisted", {}).setdefault("failed", []).append(
                {"reason": f"invalid target_raw: {exc!s}"}
            )
            return

        written: list[str] = []
        failed: list[dict[str, str]] = []
        for entry in files:
            if not isinstance(entry, dict):
                continue
            raw_path = entry.get("path")
            content = entry.get("content")
            if not raw_path or not isinstance(content, str):
                failed.append({"path": str(raw_path), "reason": "missing path or non-string content"})
                continue
            try:
                p = Path(raw_path)
                if not p.is_absolute():
                    p = (target_root / p).resolve()
                # Scope-guard: absolute paths must stay inside the workspace
                if not str(p).startswith(str(target_root)):
                    failed.append({"path": str(raw_path), "reason": "path escapes workspace"})
                    continue
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content, encoding="utf-8")
                written.append(str(p))
            except Exception as exc:
                failed.append({"path": str(raw_path), "reason": str(exc)[:200]})

        result.setdefault("files_persisted", {})
        result["files_persisted"]["written"] = written
        result["files_persisted"]["failed"] = failed
        result["files_persisted"]["target_root"] = str(target_root)

    @staticmethod
    def _build_critique(graded_contract: Any, output_schema: dict[str, Any] | None = None) -> str:
        """Build a critique string from graded contract results."""
        import json
        lines = ["Previous attempt did not meet all criteria. Issues:"]
        for r in graded_contract.results:
            status = r.status.value if hasattr(r.status, "value") else str(r.status)
            if status != "PASS":
                lines.append(f"- {r.name}: {status} — {r.reason or 'no reason given'}")
        lines.append("\nPlease address these issues in your next attempt.")
        if output_schema:
            required = output_schema.get("required", [])
            lines.append("\nYour output MUST be valid JSON matching this schema:")
            lines.append(f"```json\n{json.dumps(output_schema, indent=2)}\n```")
            if required:
                lines.append(f"Required fields: {', '.join(required)}")
            # Build a minimal valid example
            props = output_schema.get("properties", {})
            example: dict[str, Any] = {}
            for field, prop in props.items():
                ftype = prop.get("type", "string")
                if ftype == "string":
                    example[field] = f"<{field}>"
                elif ftype in ("integer", "number"):
                    example[field] = 0
                elif ftype == "boolean":
                    example[field] = False
                elif ftype == "array":
                    example[field] = []
                elif ftype == "object":
                    example[field] = {}
            if example:
                lines.append(f"\nMinimal valid example:\n```json\n{json.dumps(example, indent=2)}\n```")
        return "\n".join(lines)

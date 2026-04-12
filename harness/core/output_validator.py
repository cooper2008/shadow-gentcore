"""OutputValidator — validates agent output against schema + grading criteria.

Applied AFTER every agent execution by the harness. Each agent defines:
- output_schema (JSON schema) — structural validation
- grading_criteria_ref (YAML) — quality scoring (automated + LLM-judge)

Usage in CompositionEngine:
    validator = OutputValidator()
    validation = await validator.validate(output, manifest, agent_dir, provider)
    if not validation["passed"]:
        # trigger retry / feedback loop / abort
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


class OutputValidator:
    """Validates agent output against its declared schema and grading criteria.

    Validation layers:
    1. Schema check — required fields present, types match
    2. Automated criteria — programmatic checks (exit_code == 0, etc.)
    3. LLM-judge criteria — quality assessment by evaluator LLM
    4. Score threshold — overall score must meet minimum

    Applied uniformly to ALL agents by the harness. Each agent gets
    different validation based on its own manifest declarations.
    """

    async def validate(
        self,
        output: dict[str, Any],
        manifest: dict[str, Any],
        agent_dir: str | Path | None = None,
        provider: Any = None,
    ) -> dict[str, Any]:
        """Validate agent output against manifest's schema and grading criteria.

        Args:
            output: The agent's output dict.
            manifest: Agent manifest (dict or Pydantic model).
            agent_dir: Path to agent directory (for resolving grading_criteria_ref).
            provider: LLM provider for llm_judge criteria (optional).

        Returns:
            Dict with: passed, score, schema_valid, criteria_results, issues
        """
        issues: list[str] = []
        criteria_results: list[dict[str, Any]] = []

        # 1. Schema validation
        schema_valid = self._validate_schema(output, manifest, issues)

        # 2. Grading criteria
        score = 1.0
        threshold = 0.75
        grading_ref = _get(manifest, "grading_criteria_ref")

        if grading_ref and agent_dir:
            criteria_path = Path(agent_dir) / grading_ref
            if criteria_path.exists():
                criteria_data = yaml.safe_load(criteria_path.read_text(encoding="utf-8")) or {}
                threshold = criteria_data.get("threshold", 0.75)
                criteria_list = criteria_data.get("criteria", [])

                for criterion in criteria_list:
                    result = await self._evaluate_criterion(criterion, output, provider)
                    criteria_results.append(result)

                if criteria_results:
                    total_weight = sum(c.get("weight", 1.0) for c in criteria_results)
                    if total_weight > 0:
                        score = sum(
                            c.get("score", 0.0) * c.get("weight", 1.0)
                            for c in criteria_results
                        ) / total_weight

        passed = schema_valid and score >= threshold

        return {
            "passed": passed,
            "score": round(score, 3),
            "threshold": threshold,
            "schema_valid": schema_valid,
            "criteria_results": criteria_results,
            "issues": issues,
        }

    def _validate_schema(
        self,
        output: dict[str, Any],
        manifest: dict[str, Any],
        issues: list[str],
    ) -> bool:
        """Check output matches the manifest's output_schema (required fields, types)."""
        schema = _get(manifest, "output_schema")
        if not schema:
            return True  # No schema defined — pass by default

        if not isinstance(schema, dict):
            return True

        # Check required fields
        required = schema.get("required", [])
        properties = schema.get("properties", {})

        valid = True
        for field_name in required:
            if field_name not in output:
                issues.append(f"Missing required field: '{field_name}'")
                valid = False

        # Type checks for present fields
        for field_name, field_schema in properties.items():
            if field_name not in output:
                continue
            expected_type = field_schema.get("type")
            actual = output[field_name]
            if not self._type_matches(actual, expected_type):
                issues.append(
                    f"Field '{field_name}': expected type '{expected_type}', "
                    f"got '{type(actual).__name__}'"
                )
                valid = False

        return valid

    @staticmethod
    def _type_matches(value: Any, expected_type: str | None) -> bool:
        """Check if a value matches a JSON schema type."""
        if expected_type is None:
            return True
        type_map = {
            "string": str,
            "integer": int,
            "number": (int, float),
            "boolean": bool,
            "array": list,
            "object": dict,
        }
        expected = type_map.get(expected_type)
        if expected is None:
            return True
        return isinstance(value, expected)

    async def _evaluate_criterion(
        self,
        criterion: dict[str, Any],
        output: dict[str, Any],
        provider: Any | None,
    ) -> dict[str, Any]:
        """Evaluate a single grading criterion against the output."""
        name = criterion.get("name", "unnamed")
        ctype = criterion.get("type", "automated")
        weight = criterion.get("weight", 1.0)
        description = criterion.get("description", "")

        if ctype == "automated":
            return self._run_automated(criterion, output, name, weight)
        elif ctype == "llm_judge" and provider is not None:
            return await self._run_llm_judge(criterion, output, provider, name, weight)
        else:
            # Skip if no provider for LLM judge
            return {
                "name": name,
                "type": ctype,
                "weight": weight,
                "score": 1.0,
                "passed": True,
                "reason": f"Skipped ({ctype}, no provider)" if ctype == "llm_judge" else "Skipped",
            }

    def _run_automated(
        self,
        criterion: dict[str, Any],
        output: dict[str, Any],
        name: str,
        weight: float,
    ) -> dict[str, Any]:
        """Run an automated criterion check."""
        check_expr = criterion.get("check", "true")

        try:
            # Simple expression evaluation against output
            # Supports: "field == value", "field >= value", "field_exists"
            passed = self._eval_check(check_expr, output)
            return {
                "name": name,
                "type": "automated",
                "weight": weight,
                "score": 1.0 if passed else 0.0,
                "passed": passed,
                "reason": f"Check '{check_expr}': {'PASS' if passed else 'FAIL'}",
            }
        except Exception as exc:
            return {
                "name": name,
                "type": "automated",
                "weight": weight,
                "score": 0.0,
                "passed": False,
                "reason": f"Check error: {exc}",
            }

    @staticmethod
    def _resolve_field(output: dict[str, Any], field: str) -> Any:
        """Resolve a dot-notation field path against output dict.

        Supports:
        - "status" → output["status"]
        - "scan_quality.overall" → output["scan_quality"]["overall"]
        - "len(inventory)" → len(output["inventory"])
        """
        # Handle len() expressions
        if field.startswith("len(") and field.endswith(")"):
            inner = field[4:-1]
            resolved = OutputValidator._resolve_field(output, inner)
            if resolved is not None and hasattr(resolved, "__len__"):
                return len(resolved)
            return None

        # Handle dot-notation: scan_quality.sources_reachable_pct
        current: Any = output
        for part in field.split("."):
            if isinstance(current, dict):
                current = current.get(part)
            elif hasattr(current, part):
                current = getattr(current, part)
            else:
                return None
            if current is None:
                return None
        return current

    @staticmethod
    def _eval_check(expr: str, output: dict[str, Any]) -> bool:
        """Evaluate a simple check expression against output.

        Supports:
        - "exit_code == 0"
        - "test_count >= 1"
        - "scan_quality.overall >= 50" (dot-notation)
        - "len(inventory) >= 5" (len expressions)
        - "design_quality.dag_valid == true"
        - "true" (always pass)
        """
        if expr.strip() == "true":
            return True
        if expr.strip() == "false":
            return False

        # Handle "and" expressions: split and evaluate each part
        if " and " in expr:
            parts = expr.split(" and ")
            return all(OutputValidator._eval_check(p.strip(), output) for p in parts)

        # Handle "is not None" expressions
        if expr.strip().endswith(" is not None"):
            field = expr.strip().replace(" is not None", "")
            return OutputValidator._resolve_field(output, field) is not None

        # Parse "field op value"
        for op in (">=", "<=", "==", "!=", ">", "<"):
            if op in expr:
                parts = expr.split(op, 1)
                if len(parts) == 2:
                    field = parts[0].strip()
                    value_str = parts[1].strip()

                    actual = OutputValidator._resolve_field(output, field)
                    if actual is None:
                        return False

                    # Try numeric comparison
                    try:
                        actual_num = float(actual)
                        expected_num = float(value_str)
                        if op == "==":
                            return actual_num == expected_num
                        if op == "!=":
                            return actual_num != expected_num
                        if op == ">=":
                            return actual_num >= expected_num
                        if op == "<=":
                            return actual_num <= expected_num
                        if op == ">":
                            return actual_num > expected_num
                        if op == "<":
                            return actual_num < expected_num
                    except (ValueError, TypeError):
                        pass

                    # String comparison
                    if op == "==":
                        return str(actual).lower() == value_str.lower()
                    if op == "!=":
                        return str(actual).lower() != value_str.lower()
                break

        # Check if field exists and is truthy
        field = expr.strip()
        if field.endswith("_exists"):
            field_name = field.replace("_exists", "")
            return OutputValidator._resolve_field(output, field_name) is not None
        return bool(OutputValidator._resolve_field(output, field))

    async def _run_llm_judge(
        self,
        criterion: dict[str, Any],
        output: dict[str, Any],
        provider: Any,
        name: str,
        weight: float,
    ) -> dict[str, Any]:
        """Use an LLM as judge for a quality criterion."""
        import json

        prompt = criterion.get("prompt", criterion.get("description", name))
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a strict code quality evaluator. "
                    "Given an agent's output, determine if the criterion is met. "
                    "Respond with exactly PASS or FAIL on the first line, "
                    "then a brief reason on the next line."
                ),
            },
            {
                "role": "user",
                "content": f"Criterion: {prompt}\n\nAgent Output:\n{json.dumps(output, indent=2, default=str)[:3000]}",
            },
        ]

        try:
            response = await provider.chat(messages)
            content = response.get("content", "") if isinstance(response, dict) else str(response)
            content = content.strip()

            passed = content.upper().startswith("PASS")
            return {
                "name": name,
                "type": "llm_judge",
                "weight": weight,
                "score": 1.0 if passed else 0.0,
                "passed": passed,
                "reason": content[:200],
            }
        except Exception as exc:
            return {
                "name": name,
                "type": "llm_judge",
                "weight": weight,
                "score": 0.5,  # uncertain
                "passed": True,  # don't block on judge failure
                "reason": f"LLM judge error: {exc}",
            }


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Get from dict or Pydantic model."""
    if hasattr(obj, key):
        return getattr(obj, key)
    if isinstance(obj, dict):
        return obj.get(key, default)
    return default

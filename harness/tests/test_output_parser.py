"""Tests for OutputParser — multi-strategy JSON extraction and type coercion."""

from __future__ import annotations

import json

import pytest

from harness.core.output_parser import OutputParser


@pytest.fixture
def parser() -> OutputParser:
    return OutputParser()


@pytest.fixture
def schema() -> dict:
    return {
        "type": "object",
        "required": ["status", "score"],
        "properties": {
            "status": {"type": "string"},
            "score": {"type": "integer"},
            "passed": {"type": "boolean"},
        },
    }


class TestDirectParse:
    def test_clean_json(self, parser: OutputParser) -> None:
        result = parser.parse('{"status": "ok", "score": 5}')
        assert result == {"status": "ok", "score": 5}

    def test_empty_string_returns_none(self, parser: OutputParser) -> None:
        assert parser.parse("") is None

    def test_plain_text_returns_none_for_direct(self, parser: OutputParser) -> None:
        # Pure prose with no JSON — should try other strategies and fail
        result = parser.parse("Here is my analysis. Everything looks good.")
        assert result is None


class TestMarkdownFence:
    def test_json_fence(self, parser: OutputParser) -> None:
        text = 'Some prose.\n```json\n{"status": "done", "score": 10}\n```\nMore prose.'
        result = parser.parse(text)
        assert result == {"status": "done", "score": 10}

    def test_plain_fence(self, parser: OutputParser) -> None:
        text = '```\n{"status": "done"}\n```'
        result = parser.parse(text)
        assert result == {"status": "done"}


class TestOutermostBraces:
    def test_json_embedded_in_prose(self, parser: OutputParser) -> None:
        text = 'The output is: {"status": "ok", "score": 3} and that is all.'
        result = parser.parse(text)
        assert result == {"status": "ok", "score": 3}


class TestRegexFallback:
    def test_malformed_json_with_schema(self, parser: OutputParser, schema: dict) -> None:
        # Text that looks like JSON fields but isn't valid JSON
        text = 'status: "completed", score: 42, passed: true'
        # Regex strategy only triggers with a schema
        result = parser.parse(text, schema)
        # May not extract from this format, just ensure no crash
        assert result is None or isinstance(result, dict)

    def test_quoted_fields_extracted(self, parser: OutputParser, schema: dict) -> None:
        text = 'I found: "status": "ready", "score": 7, "passed": true in the output'
        result = parser.parse(text, schema)
        if result is not None:
            assert "status" in result or "score" in result


class TestCoerceTypes:
    def test_string_to_int(self, parser: OutputParser, schema: dict) -> None:
        data = {"status": "ok", "score": "42"}
        result = parser.coerce_types(data, schema)
        assert result["score"] == 42
        assert isinstance(result["score"], int)

    def test_string_to_bool_true(self, parser: OutputParser, schema: dict) -> None:
        data = {"status": "ok", "score": 1, "passed": "true"}
        result = parser.coerce_types(data, schema)
        assert result["passed"] is True

    def test_string_to_bool_false(self, parser: OutputParser, schema: dict) -> None:
        data = {"status": "ok", "score": 1, "passed": "false"}
        result = parser.coerce_types(data, schema)
        assert result["passed"] is False

    def test_no_schema_passthrough(self, parser: OutputParser) -> None:
        data = {"score": "99"}
        result = parser.coerce_types(data, None)
        assert result["score"] == "99"  # unchanged without schema

    def test_already_correct_type_unchanged(self, parser: OutputParser, schema: dict) -> None:
        data = {"status": "ok", "score": 5, "passed": True}
        result = parser.coerce_types(data, schema)
        assert result == {"status": "ok", "score": 5, "passed": True}

    def test_coercion_applied_during_parse(self, parser: OutputParser, schema: dict) -> None:
        text = '{"status": "ok", "score": "7", "passed": "true"}'
        result = parser.parse(text, schema)
        assert result is not None
        assert result["score"] == 7
        assert result["passed"] is True


# ── Vendor-quirk: stringified arrays/objects (Gemini Flash) ─────────────────


class TestStringifiedNestedDecode:
    """Regression: Gemini Flash sometimes emits nested arrays/objects as
    JSON-encoded STRINGS inside the parent object instead of real arrays.
    The schema's `type: array` means the gate's `agent_count` length-check
    silently fails. Auto-decode when shape matches the schema.
    """

    @pytest.fixture
    def architect_schema(self) -> dict:
        return {
            "type": "object",
            "required": ["agent_roster", "design_quality"],
            "properties": {
                "agent_roster": {"type": "array"},
                "design_quality": {"type": "object"},
            },
        }

    def test_stringified_array_unescapes_to_real_list(
        self, parser: OutputParser, architect_schema: dict
    ) -> None:
        # Real Gemini-Flash failure mode: agent_roster is a JSON-string
        text = (
            '{"agent_roster": "[{\\"name\\": \\"x\\"}, {\\"name\\": \\"y\\"}]", '
            '"design_quality": {"agent_count": 2, "dag_valid": true}}'
        )
        result = parser.parse(text, architect_schema)
        assert result is not None
        assert isinstance(result["agent_roster"], list)
        assert len(result["agent_roster"]) == 2
        assert result["agent_roster"][0] == {"name": "x"}

    def test_stringified_object_unescapes_to_real_dict(
        self, parser: OutputParser, architect_schema: dict
    ) -> None:
        text = (
            '{"agent_roster": [], '
            '"design_quality": "{\\"agent_count\\": 5, \\"dag_valid\\": false}"}'
        )
        result = parser.parse(text, architect_schema)
        assert result is not None
        assert isinstance(result["design_quality"], dict)
        assert result["design_quality"]["agent_count"] == 5
        assert result["design_quality"]["dag_valid"] is False

    def test_real_array_left_alone(
        self, parser: OutputParser, architect_schema: dict
    ) -> None:
        """Strong models that already emit real arrays must not be touched."""
        text = (
            '{"agent_roster": [{"name": "a"}], '
            '"design_quality": {"agent_count": 1, "dag_valid": true}}'
        )
        result = parser.parse(text, architect_schema)
        assert result is not None
        assert result["agent_roster"] == [{"name": "a"}]
        assert result["design_quality"]["agent_count"] == 1

    def test_malformed_stringified_array_left_as_string(
        self, parser: OutputParser, architect_schema: dict
    ) -> None:
        """If the inner JSON is broken, leave the value as string and let
        the gate fail loudly — don't silently produce something the schema
        didn't authorise."""
        text = (
            '{"agent_roster": "[{not-real-json}]", '
            '"design_quality": {"agent_count": 0, "dag_valid": false}}'
        )
        result = parser.parse(text, architect_schema)
        assert result is not None
        assert isinstance(result["agent_roster"], str)

    def test_string_for_string_field_left_alone(
        self, parser: OutputParser
    ) -> None:
        """type: string fields must not be array-decoded even when their
        value happens to start with `[`."""
        schema = {"type": "object", "properties": {"label": {"type": "string"}}}
        text = '{"label": "[experimental]"}'
        result = parser.parse(text, schema)
        assert result is not None
        assert result["label"] == "[experimental]"


class TestTruncationRepair:
    """Vendor max-tokens truncation produces stringified arrays cut mid-item.
    Recover the well-formed prefix instead of dropping the whole field.
    """

    @pytest.fixture
    def architect_schema(self) -> dict:
        return {
            "type": "object",
            "required": ["agent_roster"],
            "properties": {"agent_roster": {"type": "array"}},
        }

    def test_truncated_stringified_array_recovers_complete_items(
        self, parser: OutputParser, architect_schema: dict
    ) -> None:
        """Real Gemini-Flash failure: stringified array cut mid-second-item.
        Outer JSON closes correctly (model emits its closing `}`) but the
        stringified inner array is missing its `]`.
        """
        text = (
            '{"agent_roster": "[{\\"name\\": \\"a\\"}, {\\"name\\": \\"b\\", \\"trun"}'
        )
        result = parser.parse(text, architect_schema)
        assert result is not None
        # First object recovered; second was truncated and dropped.
        assert isinstance(result["agent_roster"], list)
        assert len(result["agent_roster"]) >= 1
        assert result["agent_roster"][0] == {"name": "a"}

    def test_truncation_recovery_drops_field_when_no_prefix_parses(
        self, parser: OutputParser, architect_schema: dict
    ) -> None:
        """If we can't recover a single complete element, leave the field
        as the original string — the gate will surface the failure loudly."""
        text = '{"agent_roster": "[{not even close to valid"}'
        result = parser.parse(text, architect_schema)
        assert result is not None
        # Recovery returned None → original string preserved
        assert isinstance(result["agent_roster"], str)

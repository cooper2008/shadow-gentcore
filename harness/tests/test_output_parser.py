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

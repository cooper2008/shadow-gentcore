"""Tests for react-mode empty-object schema-coerce guard.

Prior to this coverage the react loop accepted literal `{}` as a final
answer when output_schema was declared — `_is_json_like("{}")` returned
True because the string parses as valid JSON. That masked a common
GLM/MiniMax failure mode where the model closes a turn with empty
JSON after running tool calls, discarding its work and leaving the
downstream gate to fail with `coverage=0`.

`_satisfies_schema_shape` tightens the check: empty dicts, non-dicts,
and dicts missing every declared `required` key all trigger the
schema-coerce retry.
"""

from __future__ import annotations

from harness.core.modes.react import _is_json_like, _satisfies_schema_shape


class TestIsJsonLike:
    def test_accepts_braced(self) -> None:
        assert _is_json_like("{}") is True
        assert _is_json_like("{\"a\":1}") is True

    def test_rejects_non_braced(self) -> None:
        assert _is_json_like("plain prose") is False
        assert _is_json_like("[]") is False  # list not dict


class TestSatisfiesSchemaShape:
    def _schema(self) -> dict:
        return {"type": "object", "required": ["knowledge_map", "coverage", "gaps"]}

    def test_empty_dict_fails_required_schema(self) -> None:
        """The exact GLM/MiniMax failure mode: literal {} closing the turn."""
        assert _satisfies_schema_shape("{}", self._schema()) is False

    def test_dict_with_one_required_field_passes(self) -> None:
        assert _satisfies_schema_shape('{"knowledge_map": {}}', self._schema()) is True

    def test_dict_without_any_required_field_fails(self) -> None:
        assert _satisfies_schema_shape('{"unrelated": "stuff"}', self._schema()) is False

    def test_non_json_fails(self) -> None:
        assert _satisfies_schema_shape("not json", self._schema()) is False

    def test_empty_string_fails(self) -> None:
        assert _satisfies_schema_shape("", self._schema()) is False

    def test_none_schema_fails(self) -> None:
        """Without a schema we have no grounds to demand shape — let the
        caller's _is_json_like path handle it."""
        assert _satisfies_schema_shape("{}", None) is False

    def test_schema_without_required_accepts_empty_dict(self) -> None:
        """No `required` declared → any dict counts as satisfying."""
        assert _satisfies_schema_shape("{}", {"type": "object"}) is True

    def test_list_output_fails(self) -> None:
        """Output_schema is object — a bare list isn't a valid answer."""
        assert _satisfies_schema_shape("[]", self._schema()) is False

    def test_surrounding_whitespace_tolerated(self) -> None:
        assert _satisfies_schema_shape('  {"knowledge_map": 1}\n', self._schema()) is True

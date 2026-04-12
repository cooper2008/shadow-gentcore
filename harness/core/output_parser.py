"""OutputParser — multi-strategy JSON extraction and type coercion from LLM text."""

from __future__ import annotations

import json
import re
from typing import Any


class OutputParser:
    """Extract structured JSON from LLM output using multiple fallback strategies.

    Strategies (tried in order):
    1. Direct json.loads — works when LLM responds with clean JSON
    2. Markdown fence extraction — extracts from ```json ... ``` block
    3. Outermost brace substring — finds the first { ... } span
    4. Schema-guided regex field extraction — last resort when JSON is malformed

    After extraction, coerce_types() converts string values to proper types
    when the schema specifies int/float/bool.
    """

    def parse(self, text: str, schema: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Extract a JSON dict from text using multiple strategies.

        Args:
            text: Raw LLM output string.
            schema: Optional JSON Schema dict — used for strategy 4 field extraction.

        Returns:
            Parsed dict, or None if all strategies fail.
        """
        text = text.strip()
        if not text:
            return None

        # Strategy 1: direct parse
        result = self._try_direct(text)
        if result is not None:
            return self.coerce_types(result, schema) if schema else result

        # Strategy 2: markdown fence
        result = self._try_fence(text)
        if result is not None:
            return self.coerce_types(result, schema) if schema else result

        # Strategy 3: outermost braces
        result = self._try_braces(text)
        if result is not None:
            return self.coerce_types(result, schema) if schema else result

        # Strategy 4: schema-guided regex (only if schema given)
        if schema:
            result = self._try_regex(text, schema)
            if result is not None:
                return self.coerce_types(result, schema)

        return None

    def coerce_types(self, data: dict[str, Any], schema: dict[str, Any] | None) -> dict[str, Any]:
        """Coerce string values to types specified in the schema.

        Handles top-level properties only. Converts str→int, str→float, str→bool
        when the schema property type requires it.

        Args:
            data: Parsed dict to coerce.
            schema: JSON Schema dict with optional 'properties' mapping.

        Returns:
            New dict with coerced values (original unchanged).
        """
        if not schema:
            return data
        properties: dict[str, Any] = schema.get("properties", {})
        result = dict(data)
        for key, prop in properties.items():
            if key not in result:
                continue
            val = result[key]
            if not isinstance(val, str):
                continue
            expected_type = prop.get("type")
            if expected_type == "integer":
                try:
                    result[key] = int(val)
                except (ValueError, TypeError):
                    pass
            elif expected_type == "number":
                try:
                    result[key] = float(val)
                except (ValueError, TypeError):
                    pass
            elif expected_type == "boolean":
                if val.lower() in ("true", "1", "yes"):
                    result[key] = True
                elif val.lower() in ("false", "0", "no"):
                    result[key] = False
        return result

    # ------------------------------------------------------------------
    # Private strategy helpers
    # ------------------------------------------------------------------

    def _try_direct(self, text: str) -> dict[str, Any] | None:
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
        return None

    def _try_fence(self, text: str) -> dict[str, Any] | None:
        # Match ```json ... ``` or ``` ... ```
        pattern = r"```(?:json)?\s*(\{[\s\S]*?\})\s*```"
        match = re.search(pattern, text)
        if match:
            try:
                parsed = json.loads(match.group(1))
                if isinstance(parsed, dict):
                    return parsed
            except (json.JSONDecodeError, ValueError):
                pass
        return None

    def _try_braces(self, text: str) -> dict[str, Any] | None:
        # Find first { and last } to extract the outermost JSON object
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            try:
                parsed = json.loads(text[start:end + 1])
                if isinstance(parsed, dict):
                    return parsed
            except (json.JSONDecodeError, ValueError):
                pass
        return None

    def _try_regex(self, text: str, schema: dict[str, Any]) -> dict[str, Any] | None:
        """Last-resort: extract individual field values using regex patterns."""
        properties: dict[str, Any] = schema.get("properties", {})
        if not properties:
            return None

        result: dict[str, Any] = {}
        for field in properties:
            # Match "field": "value" or "field": 123 or "field": true/false/null
            pattern = rf'"{re.escape(field)}"\s*:\s*("(?:[^"\\]|\\.)*"|\d+(?:\.\d+)?|true|false|null|\[.*?\]|\{{.*?\}})'
            match = re.search(pattern, text, re.DOTALL)
            if match:
                raw = match.group(1)
                try:
                    result[field] = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    result[field] = raw.strip('"')

        return result if result else None

"""ValidationPipeline — lint, schema check, permission check, structural validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any


class ValidationError:
    """A single validation error."""

    def __init__(self, rule: str, message: str, severity: str = "error", path: str | None = None) -> None:
        self.rule = rule
        self.message = message
        self.severity = severity
        self.path = path

    def __repr__(self) -> str:
        return f"ValidationError(rule={self.rule!r}, message={self.message!r}, severity={self.severity!r})"


class ValidationPipeline:
    """Validates agent manifests and domain structure.

    Checks:
    - Required fields present (schema check)
    - Permission config valid
    - Structural requirements (files exist)
    - Custom lint rules
    """

    def __init__(self) -> None:
        self._rules: list[Any] = []
        self._errors: list[ValidationError] = []

    def add_rule(self, name: str, check_fn: Any) -> None:
        """Register a validation rule.

        check_fn(manifest) -> list[ValidationError] or empty list.
        """
        self._rules.append({"name": name, "check": check_fn})

    def validate(self, manifest: dict[str, Any], context: dict[str, Any] | None = None) -> list[ValidationError]:
        """Run all validation rules against a manifest.

        Returns list of ValidationError objects.
        """
        self._errors = []

        # Built-in: required fields
        self._check_required_fields(manifest)

        # Built-in: id format
        self._check_id_format(manifest)

        # Custom rules
        for rule in self._rules:
            try:
                errors = rule["check"](manifest, context or {})
                if errors:
                    self._errors.extend(errors)
            except Exception as exc:
                self._errors.append(ValidationError(
                    rule=rule["name"],
                    message=f"Rule raised exception: {exc}",
                    severity="error",
                ))

        return list(self._errors)

    def is_valid(self, manifest: dict[str, Any], context: dict[str, Any] | None = None) -> bool:
        """Check if manifest passes all validation rules."""
        errors = self.validate(manifest, context)
        return not any(e.severity == "error" for e in errors)

    def _check_required_fields(self, manifest: dict[str, Any]) -> None:
        """Check required fields are present."""
        required = ["id", "domain"]
        for field in required:
            if not manifest.get(field):
                self._errors.append(ValidationError(
                    rule="required_fields",
                    message=f"Missing required field: '{field}'",
                    severity="error",
                ))

    def _check_id_format(self, manifest: dict[str, Any]) -> None:
        """Check agent ID follows domain/name/version format."""
        agent_id = manifest.get("id", "")
        if agent_id and agent_id.count("/") < 2:
            self._errors.append(ValidationError(
                rule="id_format",
                message=f"Agent ID '{agent_id}' should follow 'domain/name/version' format",
                severity="warning",
            ))

    @property
    def errors(self) -> list[ValidationError]:
        return list(self._errors)

    @property
    def rule_count(self) -> int:
        return len(self._rules) + 2  # +2 for built-in rules

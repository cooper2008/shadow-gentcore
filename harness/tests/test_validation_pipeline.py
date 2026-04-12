"""Tests for ValidationPipeline."""

from __future__ import annotations

import pytest

from harness.core.validation_pipeline import ValidationPipeline, ValidationError


class TestValidationPipeline:
    def test_valid_manifest(self) -> None:
        pipeline = ValidationPipeline()
        manifest = {"id": "backend/CodeGen/v1", "domain": "backend"}
        errors = pipeline.validate(manifest)
        error_errors = [e for e in errors if e.severity == "error"]
        assert len(error_errors) == 0

    def test_missing_id(self) -> None:
        pipeline = ValidationPipeline()
        manifest = {"domain": "backend"}
        errors = pipeline.validate(manifest)
        assert any(e.rule == "required_fields" and "id" in e.message for e in errors)

    def test_missing_domain(self) -> None:
        pipeline = ValidationPipeline()
        manifest = {"id": "backend/Agent/v1"}
        errors = pipeline.validate(manifest)
        assert any(e.rule == "required_fields" and "domain" in e.message for e in errors)

    def test_id_format_warning(self) -> None:
        pipeline = ValidationPipeline()
        manifest = {"id": "bad-id", "domain": "backend"}
        errors = pipeline.validate(manifest)
        warnings = [e for e in errors if e.severity == "warning"]
        assert any("domain/name/version" in e.message for e in warnings)

    def test_id_format_correct_no_warning(self) -> None:
        pipeline = ValidationPipeline()
        manifest = {"id": "backend/CodeGen/v1", "domain": "backend"}
        errors = pipeline.validate(manifest)
        warnings = [e for e in errors if e.severity == "warning" and e.rule == "id_format"]
        assert len(warnings) == 0

    def test_custom_rule_pass(self) -> None:
        pipeline = ValidationPipeline()

        def check_category(manifest, ctx):
            if "category" not in manifest:
                return [ValidationError("has_category", "Missing category", "warning")]
            return []

        pipeline.add_rule("has_category", check_category)
        errors = pipeline.validate({"id": "a/b/v1", "domain": "a", "category": "fast"})
        assert not any(e.rule == "has_category" for e in errors)

    def test_custom_rule_fail(self) -> None:
        pipeline = ValidationPipeline()

        def check_category(manifest, ctx):
            if "category" not in manifest:
                return [ValidationError("has_category", "Missing category", "warning")]
            return []

        pipeline.add_rule("has_category", check_category)
        errors = pipeline.validate({"id": "a/b/v1", "domain": "a"})
        assert any(e.rule == "has_category" for e in errors)

    def test_custom_rule_exception(self) -> None:
        pipeline = ValidationPipeline()

        def bad_rule(manifest, ctx):
            raise RuntimeError("boom")

        pipeline.add_rule("bad_rule", bad_rule)
        errors = pipeline.validate({"id": "a/b/v1", "domain": "a"})
        assert any("exception" in e.message.lower() for e in errors)

    def test_is_valid(self) -> None:
        pipeline = ValidationPipeline()
        assert pipeline.is_valid({"id": "a/b/v1", "domain": "a"}) is True
        assert pipeline.is_valid({"domain": "a"}) is False

    def test_rule_count(self) -> None:
        pipeline = ValidationPipeline()
        assert pipeline.rule_count == 2  # 2 built-in
        pipeline.add_rule("custom", lambda m, c: [])
        assert pipeline.rule_count == 3

"""Tests for AgentBuilder post-write schema-drift normalizer.

Genesis LLMs (especially GLM/MiniMax via Anthropic-compat) consistently
emit two schema drifts in agent_manifest.yaml:

  1. ``constraints:`` as a list of free-form strings.
     AgentManifest expects ConstraintsConfig (a dict). With strict mode
     on, this hard-fails at load time. Without strict mode, it warns and
     the agent loads with default empty constraints.

  2. ``context.preload:`` includes invented source names like
     ``fastapi_patterns`` or ``database_patterns``. Only the names
     registered in manifest_loader._build_preload_item resolve; unknown
     entries silently produce nothing, so the agent loses context.

The normalizer (a) lifts list-shaped constraints into
``metadata.constraint_notes``, (b) drops unregistered preload entries
into ``metadata.dropped_preload_sources``. Both preserve the LLM's
intent for human readers while making the manifest schema-valid.

These tests pin the normalizer's behavior. End-to-end: a generated
manifest that previously broke ``./ai run agent`` now loads cleanly,
even with GENTCORE_STRICT_MANIFESTS=1.
"""

from __future__ import annotations

import importlib.util
from typing import Any

import pytest
import yaml


def _load_hooks_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "builder_hooks",
        "/Users/yiminguo/shadow-gentcore/agents/_genesis/AgentBuilderAgent/v1/hooks.py",
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


HOOKS = _load_hooks_module()


class TestConstraintsListNormalization:
    def test_list_constraints_lifted_to_metadata(self) -> None:
        manifest_yaml = """
id: test/Foo/v1
domain: test
category: reasoning
system_prompt_ref: system_prompt.md
constraints:
  - "Never use synchronous database calls"
  - "All endpoints must include OpenAPI docs"
"""
        out = HOOKS._normalize_agent_manifest_schema(
            "agents/Foo/v1/agent_manifest.yaml", manifest_yaml
        )
        parsed = yaml.safe_load(out)
        assert parsed["constraints"] == {}
        assert parsed["metadata"]["constraint_notes"] == [
            "Never use synchronous database calls",
            "All endpoints must include OpenAPI docs",
        ]

    def test_dict_constraints_left_alone(self) -> None:
        manifest_yaml = """
id: test/Foo/v1
domain: test
category: reasoning
system_prompt_ref: system_prompt.md
constraints:
  max_file_changes: 5
  allowed_paths:
    - "src/**"
"""
        out = HOOKS._normalize_agent_manifest_schema(
            "agents/Foo/v1/agent_manifest.yaml", manifest_yaml
        )
        parsed = yaml.safe_load(out)
        assert parsed["constraints"] == {"max_file_changes": 5, "allowed_paths": ["src/**"]}
        assert "metadata" not in parsed or "constraint_notes" not in parsed.get("metadata", {})

    def test_existing_metadata_extended_not_replaced(self) -> None:
        manifest_yaml = """
id: test/Foo/v1
domain: test
category: reasoning
system_prompt_ref: system_prompt.md
constraints:
  - "Rule one"
metadata:
  author: genesis
  tags:
    - api
"""
        out = HOOKS._normalize_agent_manifest_schema(
            "agents/Foo/v1/agent_manifest.yaml", manifest_yaml
        )
        parsed = yaml.safe_load(out)
        assert parsed["metadata"]["author"] == "genesis"
        assert parsed["metadata"]["tags"] == ["api"]
        assert parsed["metadata"]["constraint_notes"] == ["Rule one"]


class TestPreloadNormalization:
    def test_unknown_preload_dropped_to_metadata(self) -> None:
        manifest_yaml = """
id: test/Foo/v1
domain: test
category: reasoning
system_prompt_ref: system_prompt.md
context:
  preload:
    - best_practices_overlay
    - fastapi_patterns
    - database_patterns
"""
        out = HOOKS._normalize_agent_manifest_schema(
            "agents/Foo/v1/agent_manifest.yaml", manifest_yaml
        )
        parsed = yaml.safe_load(out)
        assert parsed["context"]["preload"] == ["best_practices_overlay"]
        dropped = parsed["metadata"]["dropped_preload_sources"]
        assert "fastapi_patterns" in dropped and "database_patterns" in dropped

    def test_all_valid_preload_left_alone(self) -> None:
        manifest_yaml = """
id: test/Foo/v1
domain: test
category: reasoning
system_prompt_ref: system_prompt.md
context:
  preload:
    - best_practices_overlay
    - domain_context_docs
"""
        out = HOOKS._normalize_agent_manifest_schema(
            "agents/Foo/v1/agent_manifest.yaml", manifest_yaml
        )
        parsed = yaml.safe_load(out)
        assert parsed["context"]["preload"] == ["best_practices_overlay", "domain_context_docs"]
        assert "metadata" not in parsed or "dropped_preload_sources" not in parsed.get("metadata", {})

    def test_invented_standards_name_dropped(self) -> None:
        """The 'standards' shorthand sometimes emitted by GLM — wrong name."""
        manifest_yaml = """
id: test/Foo/v1
domain: test
category: reasoning
system_prompt_ref: system_prompt.md
context:
  preload:
    - standards
"""
        out = HOOKS._normalize_agent_manifest_schema(
            "agents/Foo/v1/agent_manifest.yaml", manifest_yaml
        )
        parsed = yaml.safe_load(out)
        assert parsed["context"]["preload"] == []
        assert parsed["metadata"]["dropped_preload_sources"] == ["standards"]


class TestPermissionsNormalization:
    def test_missing_permissions_added_for_code_agent(self) -> None:
        """Code-writing agents get file_edit:allow + shell_command:allow defaults."""
        manifest_yaml = """
id: test/CodeWriter/v1
domain: test
category: codegen
system_prompt_ref: system_prompt.md
"""
        out = HOOKS._normalize_agent_manifest_schema(
            "agents/CodeWriter/v1/agent_manifest.yaml", manifest_yaml
        )
        parsed = yaml.safe_load(out)
        assert parsed["permissions"]["file_edit"] == "allow"
        assert parsed["permissions"]["shell_command"] == "allow"
        assert parsed["permissions"]["external_api"] == "deny"

    def test_missing_permissions_added_for_review_agent(self) -> None:
        """Review/analysis agents get safer defaults (file_edit:deny)."""
        manifest_yaml = """
id: test/Reviewer/v1
domain: test
category: reasoning
system_prompt_ref: system_prompt.md
"""
        out = HOOKS._normalize_agent_manifest_schema(
            "agents/Reviewer/v1/agent_manifest.yaml", manifest_yaml
        )
        parsed = yaml.safe_load(out)
        assert parsed["permissions"]["file_edit"] == "deny"
        assert parsed["permissions"]["shell_command"] == "ask"

    def test_existing_permissions_left_alone(self) -> None:
        """If the LLM already emitted a permissions block, normalizer must not overwrite."""
        manifest_yaml = """
id: test/Custom/v1
domain: test
category: codegen
system_prompt_ref: system_prompt.md
permissions:
  file_edit: ask
  shell_command: deny
  external_api: allow
  browser: allow
"""
        out = HOOKS._normalize_agent_manifest_schema(
            "agents/Custom/v1/agent_manifest.yaml", manifest_yaml
        )
        parsed = yaml.safe_load(out)
        assert parsed["permissions"]["file_edit"] == "ask"
        assert parsed["permissions"]["external_api"] == "allow"


class TestNormalizerScope:
    def test_workflow_yaml_untouched(self) -> None:
        """The schema fix MUST NOT apply to non-agent manifests."""
        wf_yaml = "name: feature_delivery\nsteps:\n  - agent: x/Foo/v1\n"
        out = HOOKS._normalize_agent_manifest_schema(
            "workflows/feature_delivery.yaml", wf_yaml
        )
        assert out == wf_yaml

    def test_grading_criteria_untouched(self) -> None:
        gc_yaml = "criteria:\n  - name: completeness\n    weight: 1.0\n"
        out = HOOKS._normalize_agent_manifest_schema(
            "agents/Foo/v1/grading_criteria.yaml", gc_yaml
        )
        assert out == gc_yaml

    def test_invalid_yaml_passes_through(self) -> None:
        bad = "constraints:\n  - this: is\n    nested: but\n  also broken: ["
        out = HOOKS._normalize_agent_manifest_schema(
            "agents/Foo/v1/agent_manifest.yaml", bad
        )
        # On parse failure we return the original unchanged for human inspection.
        assert out == bad


class TestEndToEndStrictModeLoad:
    """The whole point of the normalizer: a normalized manifest survives
    strict-mode AgentManifest.model_validate."""

    def test_normalized_manifest_passes_pydantic(self) -> None:
        from agent_contracts.manifests.agent_manifest import AgentManifest

        # Real GLM-style drift: list constraints + invented preload
        broken = """
id: test/CodeWriter/v1
domain: test
category: reasoning
system_prompt_ref: system_prompt.md
execution_mode:
  primary: react
constraints:
  - "Never use synchronous database calls"
  - "All endpoints must include OpenAPI documentation"
context:
  preload:
    - best_practices_overlay
    - fastapi_patterns
"""
        # Pre-normalization: model_validate raises
        broken_dict = yaml.safe_load(broken)
        with pytest.raises(Exception):
            AgentManifest.model_validate(broken_dict)

        # Post-normalization: passes
        fixed = HOOKS._normalize_agent_manifest_schema(
            "agents/CodeWriter/v1/agent_manifest.yaml", broken
        )
        fixed_dict = yaml.safe_load(fixed)
        AgentManifest.model_validate(fixed_dict)  # raises on failure
        # Constraint intent preserved for humans
        assert "Never use synchronous" in str(fixed_dict["metadata"]["constraint_notes"])
        # Invented preload pruned but tracked
        assert "fastapi_patterns" in fixed_dict["metadata"]["dropped_preload_sources"]

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


class TestSchemaDefaultsNormalization:
    """Drift 0b — missing input_schema / output_schema get minimal defaults.

    Gemini-Flash truncation often drops these fields when max_tokens hits
    mid-roster. Pre-fix the agent loaded but had unusable schemas; the smoke
    runner flagged them as incomplete. Now the normalizer fills minimal
    pass-through defaults so the agent is wireable.
    """

    def test_missing_input_schema_gets_default(self) -> None:
        manifest_yaml = """
id: test/Foo/v1
domain: test
category: reasoning
system_prompt_ref: system_prompt.md
"""
        out = HOOKS._normalize_agent_manifest_schema(
            "agents/Foo/v1/agent_manifest.yaml", manifest_yaml
        )
        parsed = yaml.safe_load(out)
        assert parsed["input_schema"]["type"] == "object"
        assert "Auto-defaulted" in parsed["input_schema"]["description"]

    def test_missing_output_schema_gets_default(self) -> None:
        manifest_yaml = """
id: test/Foo/v1
domain: test
category: reasoning
system_prompt_ref: system_prompt.md
"""
        out = HOOKS._normalize_agent_manifest_schema(
            "agents/Foo/v1/agent_manifest.yaml", manifest_yaml
        )
        parsed = yaml.safe_load(out)
        assert parsed["output_schema"]["type"] == "object"
        assert "Auto-defaulted" in parsed["output_schema"]["description"]

    def test_existing_schemas_left_alone(self) -> None:
        manifest_yaml = """
id: test/Foo/v1
domain: test
category: reasoning
system_prompt_ref: system_prompt.md
input_schema:
  type: object
  required: [task]
  properties:
    task: {type: string}
output_schema:
  type: object
  required: [result]
  properties:
    result: {type: string}
"""
        out = HOOKS._normalize_agent_manifest_schema(
            "agents/Foo/v1/agent_manifest.yaml", manifest_yaml
        )
        parsed = yaml.safe_load(out)
        assert parsed["input_schema"]["required"] == ["task"]
        assert parsed["output_schema"]["required"] == ["result"]


class TestAutoProviderResolution:
    """Drift 0aa — when manifest has no `provider:` block, normalizer injects
    a tier-appropriate one from config/model_tiers.yaml + available creds.

    Disabled with GENTCORE_AUTO_PROVIDER=0 so existing deterministic flows
    aren't perturbed.
    """

    def test_codegen_agent_gets_codegen_strong_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Force ANTHROPIC_API_KEY available, others absent
        for v in ("GOOGLE_API_KEY", "ZHIPU_API_KEY", "MINIMAX_API_KEY"):
            monkeypatch.delenv(v, raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.delenv("GENTCORE_AUTO_PROVIDER", raising=False)

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
        prov = parsed.get("provider")
        assert prov is not None, "auto-provider must inject when none present"
        assert prov["provider"] == "anthropic"
        assert "claude" in prov["model"].lower()
        assert prov["_resolved_tier"] == "codegen-strong"

    def test_existing_provider_block_left_alone(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Hand-set or LLM-emitted provider blocks must NOT be overwritten."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        manifest_yaml = """
id: test/CodeWriter/v1
domain: test
category: codegen
system_prompt_ref: system_prompt.md
provider:
  provider: openai
  model: my-custom-model
  api_key_env: MY_KEY
"""
        out = HOOKS._normalize_agent_manifest_schema(
            "agents/CodeWriter/v1/agent_manifest.yaml", manifest_yaml
        )
        parsed = yaml.safe_load(out)
        assert parsed["provider"]["model"] == "my-custom-model"
        assert "_resolved_tier" not in parsed["provider"]

    def test_disabled_via_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """GENTCORE_AUTO_PROVIDER=0 fully opts out — for users who want
        every agent to use the domain-wide default provider."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("GENTCORE_AUTO_PROVIDER", "0")
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
        assert "provider" not in parsed

    def test_no_creds_no_provider_block(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When no recommended model has creds set, leave the manifest
        clean — caller falls back to domain-default provider at runtime."""
        for v in ("ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "ZHIPU_API_KEY",
                  "MINIMAX_API_KEY"):
            monkeypatch.delenv(v, raising=False)
        monkeypatch.delenv("GENTCORE_AUTO_PROVIDER", raising=False)

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
        assert "provider" not in parsed


class TestSystemPromptRefAndCodegenSchema:
    """Drift 0a + Drift 0b code-aware — additional tier-2-model gaps."""

    def test_missing_system_prompt_ref_defaults_to_filename(self) -> None:
        """MiniMax M2.7 commonly omits system_prompt_ref, breaking manifest load."""
        manifest_yaml = """
id: test/Foo/v1
domain: test
category: reasoning
"""
        out = HOOKS._normalize_agent_manifest_schema(
            "agents/Foo/v1/agent_manifest.yaml", manifest_yaml
        )
        parsed = yaml.safe_load(out)
        assert parsed["system_prompt_ref"] == "system_prompt.md"

    def test_codegen_category_gets_files_array_schema(self) -> None:
        """Code-writing categories get an output_schema declaring `files: [...]`
        so AgentRunner._persist_output_files picks them up. Pre-fix the empty
        {} default left CodeWriter unable to emit files."""
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
        assert "files" in parsed["output_schema"]["required"]
        assert parsed["output_schema"]["properties"]["files"]["type"] == "array"
        item = parsed["output_schema"]["properties"]["files"]["items"]
        assert {"path", "content"} == set(item["required"])

    def test_review_category_keeps_generic_schema(self) -> None:
        """Non-code-writing categories get the simple {} default — no files array."""
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
        assert "required" not in parsed["output_schema"] or \
               parsed["output_schema"].get("required") == []
        assert "files" not in (parsed["output_schema"].get("properties") or {})

    def test_constraints_default_when_missing(self) -> None:
        """Drift 1b — missing `constraints` field gets explicit empty {}."""
        manifest_yaml = """
id: test/Foo/v1
domain: test
category: reasoning
system_prompt_ref: system_prompt.md
"""
        out = HOOKS._normalize_agent_manifest_schema(
            "agents/Foo/v1/agent_manifest.yaml", manifest_yaml
        )
        parsed = yaml.safe_load(out)
        assert parsed["constraints"] == {}


class TestWorkflowGateNormalization:
    """Drift — workflow steps missing `gate:` get a permissive default."""

    def test_missing_step_gate_filled_in(self) -> None:
        wf_yaml = """
name: feature_delivery
domain: test
steps:
  - name: analyze
    agent: test/Foo/v1
  - name: implement
    agent: test/Bar/v1
    depends_on: [analyze]
"""
        out = HOOKS._normalize_workflow_schema(
            "workflows/feature_delivery.yaml", wf_yaml
        )
        parsed = yaml.safe_load(out)
        assert parsed["steps"][0]["gate"]["name"] == "analyze_gate"
        assert parsed["steps"][0]["gate"]["condition"] == "status == success"
        assert parsed["steps"][0]["gate"]["on_fail"] == "retry"
        assert parsed["steps"][1]["gate"]["name"] == "implement_gate"

    def test_existing_gate_left_alone(self) -> None:
        wf_yaml = """
name: feature_delivery
domain: test
steps:
  - name: analyze
    agent: test/Foo/v1
    gate:
      name: my_custom_gate
      condition: "output.score >= 0.8"
      on_fail: abort
      max_retries: 0
"""
        out = HOOKS._normalize_workflow_schema(
            "workflows/feature_delivery.yaml", wf_yaml
        )
        parsed = yaml.safe_load(out)
        assert parsed["steps"][0]["gate"]["name"] == "my_custom_gate"
        assert parsed["steps"][0]["gate"]["on_fail"] == "abort"

    def test_non_workflow_path_untouched(self) -> None:
        wf_yaml = "name: feature_delivery\nsteps:\n  - name: analyze\n"
        out = HOOKS._normalize_workflow_schema(
            "agents/Foo/v1/agent_manifest.yaml", wf_yaml
        )
        assert out == wf_yaml


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

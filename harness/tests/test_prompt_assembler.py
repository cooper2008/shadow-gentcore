"""Tests for PromptAssembler."""

from __future__ import annotations

import pytest

from agent_contracts.manifests.agent_manifest import AgentManifest
from harness.core.prompt_assembler import PromptAssembler


@pytest.fixture()
def assembler() -> PromptAssembler:
    return PromptAssembler()


@pytest.fixture()
def manifest() -> AgentManifest:
    return AgentManifest(
        id="backend/CodeGenAgent/v1",
        domain="backend",
        pack="core",
        category="fast-codegen",
        system_prompt_ref="system_prompt.md",
        constraints={"max_file_changes": 5, "no_delete": True},
    )


class TestPromptAssembler:
    def test_basic_assembly(self, assembler: PromptAssembler, manifest: AgentManifest) -> None:
        messages = assembler.assemble(
            manifest=manifest,
            system_prompt_content="You are a code generation agent.",
        )
        assert len(messages) == 1
        assert messages[0]["role"] == "system"
        assert "You are a code generation agent." in messages[0]["content"]

    def test_includes_constraints(self, assembler: PromptAssembler, manifest: AgentManifest) -> None:
        messages = assembler.assemble(
            manifest=manifest,
            system_prompt_content="System prompt.",
        )
        system_content = messages[0]["content"]
        assert "Constraints" in system_content
        assert "max_file_changes" in system_content

    def test_includes_tools(self, assembler: PromptAssembler, manifest: AgentManifest) -> None:
        messages = assembler.assemble(
            manifest=manifest,
            system_prompt_content="System prompt.",
            tool_descriptions=[
                {"name": "pytest", "description": "Run Python tests"},
                {"name": "ruff", "description": "Lint Python code"},
            ],
        )
        system_content = messages[0]["content"]
        assert "Available Tools" in system_content
        assert "pytest" in system_content
        assert "ruff" in system_content

    def test_includes_context(self, assembler: PromptAssembler, manifest: AgentManifest) -> None:
        messages = assembler.assemble(
            manifest=manifest,
            system_prompt_content="System prompt.",
            context_items=[
                {"source": "repo_map", "content": "src/ contains main code"},
            ],
        )
        system_content = messages[0]["content"]
        assert "Context" in system_content
        assert "repo_map" in system_content

    def test_task_input_as_user_message(self, assembler: PromptAssembler, manifest: AgentManifest) -> None:
        messages = assembler.assemble(
            manifest=manifest,
            system_prompt_content="System prompt.",
            task_input={"instruction": "Add error handling to main.py"},
        )
        assert len(messages) == 2
        assert messages[1]["role"] == "user"
        assert "Add error handling" in messages[1]["content"]

    def test_task_input_prompt_key(self, assembler: PromptAssembler, manifest: AgentManifest) -> None:
        messages = assembler.assemble(
            manifest=manifest,
            system_prompt_content="System prompt.",
            task_input={"prompt": "Fix the bug"},
        )
        assert messages[1]["content"] == "Fix the bug"

    def test_task_input_fallback_json(self, assembler: PromptAssembler, manifest: AgentManifest) -> None:
        messages = assembler.assemble(
            manifest=manifest,
            system_prompt_content="System prompt.",
            task_input={"custom_key": "custom_value"},
        )
        assert "custom_key" in messages[1]["content"]
        assert "custom_value" in messages[1]["content"]

    def test_all_sections_present(self, assembler: PromptAssembler, manifest: AgentManifest) -> None:
        messages = assembler.assemble(
            manifest=manifest,
            system_prompt_content="Base system prompt.",
            tool_descriptions=[{"name": "tool1", "description": "desc"}],
            context_items=[{"source": "src", "content": "ctx"}],
            task_input={"instruction": "Do the thing"},
        )
        system_content = messages[0]["content"]
        assert "Base system prompt." in system_content
        assert "Available Tools" in system_content
        assert "Constraints" in system_content
        assert "Context" in system_content
        assert messages[1]["role"] == "user"

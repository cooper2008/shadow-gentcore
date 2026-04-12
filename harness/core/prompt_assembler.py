"""PromptAssembler — combines manifest, tools, constraints, permissions, context, and task into LLM messages."""

from __future__ import annotations

from typing import Any

from agent_contracts.manifests.agent_manifest import AgentManifest


class PromptAssembler:
    """Assembles the final LLM message list from an agent manifest and task context.

    Sections assembled (in order):
    1. System prompt (from manifest system_prompt_ref content)
    2. Tool descriptions (from resolved tool bindings)
    3. Constraints (from manifest constraints)
    4. Permissions (from manifest permissions)
    5. Context (workspace, repo knowledge, prior artifacts)
    6. Task (from TaskEnvelope input_payload)
    """

    def assemble(
        self,
        manifest: AgentManifest,
        system_prompt_content: str,
        tool_descriptions: list[dict[str, Any]] | None = None,
        context_items: list[dict[str, str]] | None = None,
        task_input: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Assemble the full message list for an LLM call.

        Returns a list of message dicts with 'role' and 'content' keys,
        ready for provider consumption.
        """
        messages: list[dict[str, Any]] = []

        # 1. System prompt
        system_parts: list[str] = [system_prompt_content]

        # 2. Tool descriptions
        if tool_descriptions:
            tool_section = self._format_tools(tool_descriptions)
            system_parts.append(tool_section)

        # 3. Constraints
        constraints = getattr(manifest, "constraints", None) or (manifest.get("constraints") if isinstance(manifest, dict) else None)
        if constraints:
            constraints_section = self._format_constraints(constraints)
            system_parts.append(constraints_section)

        # 4. Permissions
        permissions = getattr(manifest, "permissions", None) or (manifest.get("permissions") if isinstance(manifest, dict) else None)
        if permissions:
            permissions_section = self._format_permissions(permissions)
            system_parts.append(permissions_section)

        # 5. Context items
        if context_items:
            context_section = self._format_context(context_items)
            system_parts.append(context_section)

        # 6. Output schema (from explicit param or manifest)
        if output_schema is None and isinstance(manifest, dict):
            output_schema = manifest.get("output_schema")
        if output_schema:
            system_parts.append(self._format_output_schema(output_schema))

        messages.append({
            "role": "system",
            "content": "\n\n".join(system_parts),
        })

        # 6. Task input as user message
        if task_input:
            messages.append({
                "role": "user",
                "content": self._format_task(task_input),
            })

        return messages

    def _format_output_schema(self, schema: dict[str, Any]) -> str:
        """Format the output schema into a system prompt section."""
        import json
        lines = [
            "## Required Output Format",
            "Your ENTIRE response must be valid JSON matching this schema exactly.",
            "No prose, no markdown, no explanation — only the JSON object.",
            "",
            "```json",
            json.dumps(schema, indent=2),
            "```",
        ]
        # Add required fields summary if present
        required = schema.get("required", [])
        if required:
            lines.append("")
            lines.append(f"Required fields: {', '.join(required)}")
        return "\n".join(lines)

    def _format_tools(self, tool_descriptions: list[dict[str, Any]]) -> str:
        """Format tool descriptions into a system prompt section."""
        lines = ["## Available Tools"]
        for tool in tool_descriptions:
            name = tool.get("name", "unknown")
            desc = tool.get("description", "")
            lines.append(f"- **{name}**: {desc}")
        return "\n".join(lines)

    def _format_constraints(self, constraints: Any) -> str:
        """Format constraints into a system prompt section."""
        lines = ["## Constraints"]
        if hasattr(constraints, "model_dump"):
            constraint_dict = constraints.model_dump(exclude_defaults=True)
        elif isinstance(constraints, dict):
            constraint_dict = constraints
        else:
            return "## Constraints\n- (see manifest)"
        for key, value in constraint_dict.items():
            lines.append(f"- {key}: {value}")
        return "\n".join(lines)

    def _format_permissions(self, permissions: Any) -> str:
        """Format permissions into a system prompt section."""
        lines = ["## Permissions"]
        if hasattr(permissions, "model_dump"):
            perm_dict = permissions.model_dump()
        elif isinstance(permissions, dict):
            perm_dict = permissions
        else:
            return "## Permissions\n- (see manifest)"
        for key, value in perm_dict.items():
            lines.append(f"- {key}: {value}")
        return "\n".join(lines)

    def _format_context(self, context_items: list[dict[str, str]]) -> str:
        """Format context items into a system prompt section."""
        lines = ["## Context"]
        for item in context_items:
            source = item.get("source", "unknown")
            content = item.get("content", "")
            lines.append(f"### {source}\n{content}")
        return "\n".join(lines)

    def _format_task(self, task_input: dict[str, Any]) -> str:
        """Format task input into a user message."""
        if "instruction" in task_input:
            return str(task_input["instruction"])
        if "prompt" in task_input:
            return str(task_input["prompt"])
        # Fallback: serialize the dict
        import json
        return json.dumps(task_input, indent=2, default=str)

"""ClaudeCodeProvider — uses the local Claude Code CLI with subscription auth.

Routes LLM calls through `claude -p` which uses the user's Claude Code
subscription (Max/Pro/Team) instead of a separate API key.

Requires: `claude` CLI installed and logged in.
ANTHROPIC_API_KEY is removed so Claude Code falls back to subscription auth.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, AsyncIterator

from harness.providers.base_provider import BaseProvider, LLMChunk


class ClaudeCodeProvider(BaseProvider):
    """Routes LLM calls through the local Claude Code CLI subscription."""

    def __init__(self, model: str = "", timeout: int = 600) -> None:
        self._model = model
        self._timeout = timeout

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        """Send messages through claude -p via stdin."""
        # Combine system + user messages into one prompt for claude -p
        system_parts: list[str] = []
        user_parts: list[str] = []

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")

            if role == "system":
                if isinstance(content, str):
                    system_parts.append(content)
            elif role == "user":
                if isinstance(content, str):
                    user_parts.append(content)
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict):
                            user_parts.append(block.get("content", str(block)))
            elif role == "assistant":
                if isinstance(content, str) and content:
                    user_parts.append(f"[Prior output]: {content[:2000]}")

        # Build a single prompt: system context first, then user request
        full_prompt_parts: list[str] = []
        if system_parts:
            # Truncate system prompt to keep total manageable
            system_text = "\n\n".join(system_parts)[:8000]
            full_prompt_parts.append(f"<system>\n{system_text}\n</system>")
        full_prompt_parts.extend(user_parts)
        full_prompt = "\n\n".join(full_prompt_parts)

        # Truncate total prompt to avoid overwhelming claude -p
        if len(full_prompt) > 50000:
            full_prompt = full_prompt[:50000] + "\n\n[Context truncated. Produce your output now.]"

        # Build command — NO --system-prompt flag (everything goes through stdin)
        cmd = ["claude", "-p", "--dangerously-skip-permissions", "--max-turns", "50"]

        # Remove ANTHROPIC_API_KEY so claude uses subscription auth
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=full_prompt.encode("utf-8")),
                timeout=self._timeout,
            )
            content_out = stdout.decode("utf-8", errors="replace").strip()

            if proc.returncode != 0:
                err = stderr.decode("utf-8", errors="replace").strip()
                if err and not content_out:
                    content_out = f"Error from claude CLI: {err}"

        except asyncio.TimeoutError:
            content_out = f"Error: claude CLI timed out after {self._timeout}s"
        except Exception as exc:
            content_out = f"Error: {exc}"

        return {
            "content": content_out,
            "tokens_used": len(full_prompt.split()) + len(content_out.split()),
            "tool_calls": [],
            "model": "claude-code-subscription",
        }

    async def stream(self, messages: list[dict[str, Any]], **kwargs: Any) -> AsyncIterator[LLMChunk]:
        response = await self.chat(messages, **kwargs)
        yield LLMChunk(
            content=response["content"],
            delta=response["content"],
            is_final=True,
            tokens_used=response.get("tokens_used", 0),
        )

    @property
    def provider_name(self) -> str:
        return "claudecode"

    @property
    def default_model(self) -> str:
        return "claude-code-subscription"

"""Tests for DryRunProvider."""

from __future__ import annotations

import pytest

from harness.providers.dry_run import DryRunProvider


class TestDryRunProvider:
    @pytest.mark.asyncio
    async def test_chat_returns_dict(self) -> None:
        provider = DryRunProvider()
        messages = [
            {"role": "system", "content": "You are TestAgent."},
            {"role": "user", "content": "Do something useful."},
        ]
        resp = await provider.chat(messages)
        assert isinstance(resp, dict)
        assert "DRY RUN" in resp["content"]
        assert resp["tokens_used"] == 0
        assert resp["model"] == "dry-run"
        assert resp["tool_calls"] == []

    @pytest.mark.asyncio
    async def test_chat_includes_agent_identity(self) -> None:
        provider = DryRunProvider()
        messages = [
            {"role": "system", "content": "You are FastAPICodeGenAgent."},
            {"role": "user", "content": "Generate a health endpoint."},
        ]
        resp = await provider.chat(messages)
        assert "FastAPICodeGenAgent" in resp["content"]

    @pytest.mark.asyncio
    async def test_chat_includes_task_summary(self) -> None:
        provider = DryRunProvider()
        messages = [{"role": "user", "content": "Build a REST API for orders."}]
        resp = await provider.chat(messages)
        assert "Build a REST API" in resp["content"]

    @pytest.mark.asyncio
    async def test_stream_yields_single_chunk(self) -> None:
        provider = DryRunProvider()
        messages = [{"role": "user", "content": "test"}]
        chunks = []
        async for chunk in provider.stream(messages):
            chunks.append(chunk)
        assert len(chunks) == 1
        assert chunks[0].is_final is True
        assert "DRY RUN" in chunks[0].content

    def test_provider_name(self) -> None:
        assert DryRunProvider().provider_name == "dry_run"

    def test_default_model(self) -> None:
        assert DryRunProvider().default_model == "dry-run"

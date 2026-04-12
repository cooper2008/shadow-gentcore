"""Tests for BaseProvider interface and dataclasses."""

from __future__ import annotations

from typing import Any, AsyncIterator

import pytest

from harness.providers.base_provider import BaseProvider, LLMResponse, LLMChunk


class TestLLMResponse:
    def test_defaults(self) -> None:
        resp = LLMResponse()
        assert resp.content == ""
        assert resp.tokens_used == 0
        assert resp.tool_calls == []
        assert resp.model == ""
        assert resp.stop_reason is None
        assert resp.raw == {}

    def test_with_values(self) -> None:
        resp = LLMResponse(
            content="hello",
            tokens_used=100,
            tool_calls=[{"id": "tc-1", "name": "test"}],
            model="claude-3",
            stop_reason="end_turn",
        )
        assert resp.content == "hello"
        assert resp.tokens_used == 100
        assert len(resp.tool_calls) == 1


class TestLLMChunk:
    def test_defaults(self) -> None:
        chunk = LLMChunk()
        assert chunk.content == ""
        assert chunk.delta == ""
        assert chunk.is_final is False

    def test_final_chunk(self) -> None:
        chunk = LLMChunk(is_final=True, tokens_used=50)
        assert chunk.is_final is True
        assert chunk.tokens_used == 50


class TestBaseProvider:
    def test_cannot_instantiate_abstract(self) -> None:
        with pytest.raises(TypeError):
            BaseProvider()

    def test_concrete_implementation(self) -> None:
        class MockProvider(BaseProvider):
            async def chat(self, messages, **kwargs):
                return LLMResponse(content="mock", tokens_used=10)

            async def stream(self, messages, **kwargs):
                yield LLMChunk(content="mock", is_final=True)

            @property
            def provider_name(self) -> str:
                return "mock"

            @property
            def default_model(self) -> str:
                return "mock-model"

        provider = MockProvider()
        assert provider.provider_name == "mock"
        assert provider.default_model == "mock-model"

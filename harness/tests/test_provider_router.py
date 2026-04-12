"""Tests for ProviderRouter."""

from __future__ import annotations

from typing import Any, AsyncIterator

import pytest

from harness.providers.base_provider import BaseProvider, LLMResponse, LLMChunk
from harness.providers.router import ProviderRouter


class FakeProvider(BaseProvider):
    def __init__(self, name: str) -> None:
        self._name = name

    async def chat(self, messages, **kwargs):
        return LLMResponse(content=f"from {self._name}")

    async def stream(self, messages, **kwargs):
        yield LLMChunk(content="chunk", is_final=True)

    @property
    def provider_name(self) -> str:
        return self._name

    @property
    def default_model(self) -> str:
        return f"{self._name}-model"


class TestProviderRouter:
    def test_route_to_default(self) -> None:
        default = FakeProvider("default")
        router = ProviderRouter(default_provider=default)
        provider = router.route("backend/Agent/v1")
        assert provider.provider_name == "default"

    def test_route_by_category(self) -> None:
        router = ProviderRouter()
        anthropic = FakeProvider("anthropic")
        openai = FakeProvider("openai")
        router.register_provider("anthropic", anthropic)
        router.register_provider("openai", openai)
        router.set_category_mapping("fast-codegen", "anthropic")
        router.set_category_mapping("reasoning", "openai")

        p = router.route("backend/Agent/v1", category="fast-codegen")
        assert p.provider_name == "anthropic"
        p = router.route("backend/Agent/v1", category="reasoning")
        assert p.provider_name == "openai"

    def test_route_agent_override(self) -> None:
        router = ProviderRouter()
        anthropic = FakeProvider("anthropic")
        openai = FakeProvider("openai")
        router.register_provider("anthropic", anthropic)
        router.register_provider("openai", openai)
        router.set_category_mapping("fast-codegen", "anthropic")
        router.set_agent_override("special/Agent/v1", "openai")

        p = router.route("special/Agent/v1", category="fast-codegen")
        assert p.provider_name == "openai"  # agent override wins

    def test_route_explicit_override(self) -> None:
        router = ProviderRouter()
        anthropic = FakeProvider("anthropic")
        openai = FakeProvider("openai")
        router.register_provider("anthropic", anthropic)
        router.register_provider("openai", openai)

        p = router.route("any/Agent/v1", provider_override="openai")
        assert p.provider_name == "openai"

    def test_route_no_provider_raises(self) -> None:
        router = ProviderRouter()
        with pytest.raises(ValueError, match="No provider found"):
            router.route("unknown/Agent/v1")

    def test_available_providers(self) -> None:
        router = ProviderRouter()
        router.register_provider("a", FakeProvider("a"))
        router.register_provider("b", FakeProvider("b"))
        assert sorted(router.available_providers) == ["a", "b"]

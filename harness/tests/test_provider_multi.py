"""Tests for multi-provider routing: fallback chains, capability-based routing, OpenAI/Bedrock providers."""

from __future__ import annotations

from typing import Any, AsyncIterator

import pytest

from harness.providers.base_provider import BaseProvider, LLMResponse, LLMChunk
from harness.providers.router import ProviderRouter
from harness.providers.openai_provider import OpenAIProvider
from harness.providers.bedrock_provider import BedrockProvider


class FakeProvider(BaseProvider):
    """Minimal provider for testing routing logic."""

    def __init__(self, name: str, model: str = "fake-model") -> None:
        self._name = name
        self._model = model

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> LLMResponse:
        return LLMResponse(content=f"response from {self._name}", model=self._model)

    async def stream(self, messages: list[dict[str, Any]], **kwargs: Any) -> AsyncIterator[LLMChunk]:
        yield LLMChunk(content="chunk", is_final=True)

    @property
    def provider_name(self) -> str:
        return self._name

    @property
    def default_model(self) -> str:
        return self._model


class TestMultiProviderRouter:
    def test_capability_based_routing(self) -> None:
        router = ProviderRouter()
        vision_provider = FakeProvider("vision_llm")
        code_provider = FakeProvider("code_llm")
        router.register_provider("vision_llm", vision_provider)
        router.register_provider("code_llm", code_provider)
        router.set_capability_mapping("vision", "vision_llm")
        router.set_capability_mapping("tool_use", "code_llm")

        result = router.route("agent/A/v1", required_capabilities=["vision"])
        assert result.provider_name == "vision_llm"

        result = router.route("agent/B/v1", required_capabilities=["tool_use"])
        assert result.provider_name == "code_llm"

    def test_fallback_chain(self) -> None:
        router = ProviderRouter()
        primary = FakeProvider("anthropic")
        fallback1 = FakeProvider("openai")
        fallback2 = FakeProvider("bedrock")
        router.register_provider("anthropic", primary)
        router.register_provider("openai", fallback1)
        router.register_provider("bedrock", fallback2)
        router.set_fallback_chain("anthropic", ["openai", "bedrock"])

        providers = router.route_with_fallback("agent/A/v1", provider_override="anthropic")
        assert len(providers) == 3
        assert providers[0].provider_name == "anthropic"
        assert providers[1].provider_name == "openai"
        assert providers[2].provider_name == "bedrock"

    def test_fallback_chain_no_fallbacks(self) -> None:
        router = ProviderRouter()
        solo = FakeProvider("solo")
        router.register_provider("solo", solo)

        providers = router.route_with_fallback("agent/A/v1", provider_override="solo")
        assert len(providers) == 1
        assert providers[0].provider_name == "solo"

    def test_route_different_agents_different_providers(self) -> None:
        router = ProviderRouter()
        anthropic = FakeProvider("anthropic")
        openai = FakeProvider("openai")
        router.register_provider("anthropic", anthropic)
        router.register_provider("openai", openai)
        router.set_agent_override("backend/CodeGenAgent/v1", "anthropic")
        router.set_agent_override("backend/ReviewAgent/v1", "openai")

        assert router.route("backend/CodeGenAgent/v1").provider_name == "anthropic"
        assert router.route("backend/ReviewAgent/v1").provider_name == "openai"

    def test_category_routing(self) -> None:
        router = ProviderRouter()
        fast = FakeProvider("fast")
        smart = FakeProvider("smart")
        router.register_provider("fast", fast)
        router.register_provider("smart", smart)
        router.set_category_mapping("code_generation", "smart")
        router.set_category_mapping("validation", "fast")

        assert router.route("a/b/v1", category="code_generation").provider_name == "smart"
        assert router.route("a/b/v1", category="validation").provider_name == "fast"

    def test_resolution_order(self) -> None:
        """Explicit override > agent override > capability > category > default."""
        router = ProviderRouter(default_provider=FakeProvider("default"))
        override = FakeProvider("override")
        agent_p = FakeProvider("agent_specific")
        cap_p = FakeProvider("cap_provider")
        cat_p = FakeProvider("cat_provider")
        router.register_provider("override", override)
        router.register_provider("agent_specific", agent_p)
        router.register_provider("cap_provider", cap_p)
        router.register_provider("cat_provider", cat_p)

        router.set_agent_override("a/B/v1", "agent_specific")
        router.set_capability_mapping("vision", "cap_provider")
        router.set_category_mapping("code", "cat_provider")

        # Explicit override wins
        assert router.route("a/B/v1", category="code", provider_override="override").provider_name == "override"
        # Agent override next
        assert router.route("a/B/v1", category="code").provider_name == "agent_specific"
        # Capability next (different agent)
        assert router.route("a/C/v1", required_capabilities=["vision"]).provider_name == "cap_provider"
        # Category next
        assert router.route("a/D/v1", category="code").provider_name == "cat_provider"
        # Default last
        assert router.route("a/E/v1").provider_name == "default"


class TestOpenAIProviderStructure:
    def test_provider_name(self) -> None:
        p = OpenAIProvider(api_key="test")
        assert p.provider_name == "openai"

    def test_default_model(self) -> None:
        p = OpenAIProvider(api_key="test", model="gpt-4o-mini")
        assert p.default_model == "gpt-4o-mini"


class TestBedrockProviderStructure:
    def test_provider_name(self) -> None:
        p = BedrockProvider(region="us-west-2")
        assert p.provider_name == "bedrock"

    def test_default_model(self) -> None:
        p = BedrockProvider(model_id="anthropic.claude-3-haiku-20240307-v1:0")
        assert p.default_model == "anthropic.claude-3-haiku-20240307-v1:0"

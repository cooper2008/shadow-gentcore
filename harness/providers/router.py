"""ProviderRouter — routes agents to LLM providers based on category config."""

from __future__ import annotations

from typing import Any

from harness.providers.base_provider import BaseProvider


class ProviderRouter:
    """Routes agent requests to the appropriate LLM provider.

    MVP: single-provider (Anthropic). Supports per-agent overrides.
    Category → config → provider selection.
    """

    def __init__(self, default_provider: BaseProvider | None = None) -> None:
        self._default_provider = default_provider
        self._providers: dict[str, BaseProvider] = {}
        self._category_map: dict[str, str] = {}
        self._agent_overrides: dict[str, str] = {}
        self._fallback_chains: dict[str, list[str]] = {}
        self._capability_map: dict[str, str] = {}

    def register_provider(self, name: str, provider: BaseProvider) -> None:
        """Register a named provider."""
        self._providers[name] = provider

    def set_category_mapping(self, category: str, provider_name: str) -> None:
        """Map a category to a provider name."""
        self._category_map[category] = provider_name

    def set_agent_override(self, agent_id: str, provider_name: str) -> None:
        """Override provider for a specific agent."""
        self._agent_overrides[agent_id] = provider_name

    def set_fallback_chain(self, primary: str, fallbacks: list[str]) -> None:
        """Set a fallback chain for a provider. If primary fails, try fallbacks in order."""
        self._fallback_chains[primary] = fallbacks

    def set_capability_mapping(self, capability: str, provider_name: str) -> None:
        """Map a capability (e.g., 'tool_use', 'streaming', 'vision') to a provider."""
        self._capability_map[capability] = provider_name

    def route(
        self,
        agent_id: str,
        category: str | None = None,
        provider_override: str | None = None,
        required_capabilities: list[str] | None = None,
    ) -> BaseProvider:
        """Route an agent to the appropriate provider.

        Resolution order:
        1. Explicit provider_override
        2. Agent-specific override
        3. Capability-based routing
        4. Category mapping
        5. Default provider

        Raises ValueError if no provider can be resolved.
        """
        # 1. Explicit override from task
        if provider_override and provider_override in self._providers:
            return self._providers[provider_override]

        # 2. Agent-specific override
        if agent_id in self._agent_overrides:
            name = self._agent_overrides[agent_id]
            if name in self._providers:
                return self._providers[name]

        # 3. Capability-based routing
        if required_capabilities:
            for cap in required_capabilities:
                if cap in self._capability_map:
                    name = self._capability_map[cap]
                    if name in self._providers:
                        return self._providers[name]

        # 4. Category mapping
        if category and category in self._category_map:
            name = self._category_map[category]
            if name in self._providers:
                return self._providers[name]

        # 5. Default
        if self._default_provider is not None:
            return self._default_provider

        raise ValueError(
            f"No provider found for agent '{agent_id}' "
            f"(category={category}, override={provider_override})"
        )

    def route_with_fallback(
        self,
        agent_id: str,
        category: str | None = None,
        provider_override: str | None = None,
    ) -> list[BaseProvider]:
        """Route with fallback chain — returns ordered list of providers to try.

        First element is the primary, rest are fallbacks.
        """
        primary = self.route(agent_id, category, provider_override)
        result = [primary]

        # Find fallbacks for the primary's name
        primary_name = primary.provider_name
        if primary_name in self._fallback_chains:
            for fb_name in self._fallback_chains[primary_name]:
                if fb_name in self._providers:
                    result.append(self._providers[fb_name])

        return result

    @property
    def available_providers(self) -> list[str]:
        return list(self._providers.keys())

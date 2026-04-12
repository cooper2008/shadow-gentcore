"""BrowserBridge — stub interface for UI inspection."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class BrowserBridge:
    """Stub bridge for browser/UI inspection.

    MVP: logs all calls without actual browser interaction.
    Future: integrates with Playwright or similar for real UI testing.
    """

    def __init__(self) -> None:
        self._action_log: list[dict[str, Any]] = []

    def navigate(self, url: str) -> dict[str, Any]:
        """Navigate to a URL (stub)."""
        logger.info("BrowserBridge.navigate: %s", url)
        result = {"action": "navigate", "url": url, "status": "stub"}
        self._action_log.append(result)
        return result

    def screenshot(self, selector: str | None = None) -> dict[str, Any]:
        """Take a screenshot (stub)."""
        logger.info("BrowserBridge.screenshot: selector=%s", selector)
        result = {"action": "screenshot", "selector": selector, "status": "stub", "path": None}
        self._action_log.append(result)
        return result

    def get_text(self, selector: str) -> dict[str, Any]:
        """Get text content of an element (stub)."""
        logger.info("BrowserBridge.get_text: %s", selector)
        result = {"action": "get_text", "selector": selector, "status": "stub", "text": ""}
        self._action_log.append(result)
        return result

    def click(self, selector: str) -> dict[str, Any]:
        """Click an element (stub)."""
        logger.info("BrowserBridge.click: %s", selector)
        result = {"action": "click", "selector": selector, "status": "stub"}
        self._action_log.append(result)
        return result

    def evaluate(self, script: str) -> dict[str, Any]:
        """Evaluate JavaScript (stub)."""
        logger.info("BrowserBridge.evaluate: %s", script[:50])
        result = {"action": "evaluate", "script": script, "status": "stub", "result": None}
        self._action_log.append(result)
        return result

    @property
    def action_log(self) -> list[dict[str, Any]]:
        return list(self._action_log)

    def clear_log(self) -> None:
        self._action_log.clear()

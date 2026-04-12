"""CI/CD hook interface — triggers workflows from CI events."""

from __future__ import annotations

import logging
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)


class CIHook:
    """Represents a single CI/CD hook binding a CI event to a workflow trigger."""

    def __init__(
        self,
        event: str,
        workflow_name: str,
        filter_fn: Callable[[dict[str, Any]], bool] | None = None,
    ) -> None:
        self.event = event
        self.workflow_name = workflow_name
        self.filter_fn = filter_fn or (lambda _: True)


class CIHookRegistry:
    """Registry for CI/CD hooks that trigger agent workflows from CI events.

    Supported events: push, pull_request, merge, tag, schedule, manual.
    """

    def __init__(self) -> None:
        self._hooks: list[CIHook] = []
        self._trigger_log: list[dict[str, Any]] = []

    def register(
        self,
        event: str,
        workflow_name: str,
        filter_fn: Callable[[dict[str, Any]], bool] | None = None,
    ) -> None:
        """Register a hook that triggers a workflow on a CI event."""
        self._hooks.append(CIHook(event=event, workflow_name=workflow_name, filter_fn=filter_fn))
        logger.info("Registered CI hook: %s -> %s", event, workflow_name)

    async def process_event(
        self,
        event: str,
        payload: dict[str, Any],
        executor: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
    ) -> list[dict[str, Any]]:
        """Process a CI event and trigger matching workflows.

        Args:
            event: CI event type (e.g., 'push', 'pull_request').
            payload: Event payload with metadata.
            executor: Async function to execute a workflow by name.

        Returns:
            List of trigger results.
        """
        results: list[dict[str, Any]] = []
        matching = [h for h in self._hooks if h.event == event and h.filter_fn(payload)]

        for hook in matching:
            trigger_record = {
                "event": event,
                "workflow": hook.workflow_name,
                "payload_keys": list(payload.keys()),
            }

            if executor:
                try:
                    exec_result = await executor(hook.workflow_name, payload)
                    trigger_record["status"] = "executed"
                    trigger_record["result"] = exec_result
                except Exception as exc:
                    trigger_record["status"] = "error"
                    trigger_record["error"] = str(exc)
            else:
                trigger_record["status"] = "no_executor"

            self._trigger_log.append(trigger_record)
            results.append(trigger_record)
            logger.info("CI hook triggered: %s -> %s (%s)", event, hook.workflow_name, trigger_record["status"])

        return results

    @property
    def hooks(self) -> list[CIHook]:
        return list(self._hooks)

    @property
    def trigger_log(self) -> list[dict[str, Any]]:
        return list(self._trigger_log)

    def clear(self) -> None:
        self._hooks.clear()
        self._trigger_log.clear()

"""Runtime abstractions — BaseRuntime and LocalRuntime."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class BaseRuntime(ABC):
    """Abstract runtime interface for agent execution environments.

    Concrete implementations provide environment-specific behavior
    for workspace resolution, credentials, permissions, output, and storage.
    """

    @abstractmethod
    def resolve_workspace(self, domain: str) -> Path:
        """Resolve the workspace directory for a domain."""
        ...

    @abstractmethod
    def resolve_credentials(self, provider: str) -> dict[str, str]:
        """Resolve credentials for an LLM provider."""
        ...

    @abstractmethod
    def resolve_permissions(self, action: str, agent_id: str) -> str:
        """Resolve permission for an action. Returns 'allow', 'deny', or 'ask'."""
        ...

    @abstractmethod
    def get_output_mode(self) -> str:
        """Get the output mode (e.g., 'console', 'json', 'silent')."""
        ...

    @abstractmethod
    def get_storage_backend(self) -> Any:
        """Get the storage backend instance."""
        ...


class LocalRuntime(BaseRuntime):
    """Local development runtime.

    - Interactive permissions (defaults to 'ask')
    - Filesystem-based workspace
    - Environment variable credentials
    - Console output mode
    """

    def __init__(
        self,
        workspace_root: str | Path | None = None,
        output_mode: str = "console",
        storage_backend: Any = None,
    ) -> None:
        self._workspace_root = Path(workspace_root) if workspace_root else Path.cwd()
        self._output_mode = output_mode
        self._storage_backend = storage_backend

    def resolve_workspace(self, domain: str) -> Path:
        """Resolve workspace to a domain subdirectory under workspace root."""
        workspace = self._workspace_root / domain
        workspace.mkdir(parents=True, exist_ok=True)
        return workspace

    def resolve_credentials(self, provider: str) -> dict[str, str]:
        """Resolve credentials from environment variables.

        Convention: PROVIDER_API_KEY (e.g., ANTHROPIC_API_KEY, OPENAI_API_KEY).
        """
        env_key = f"{provider.upper()}_API_KEY"
        api_key = os.environ.get(env_key, "")
        return {"api_key": api_key, "provider": provider}

    def resolve_permissions(self, action: str, agent_id: str) -> str:
        """Local mode: default to 'ask' for interactive approval."""
        return "ask"

    def get_output_mode(self) -> str:
        return self._output_mode

    def get_storage_backend(self) -> Any:
        return self._storage_backend

    @property
    def workspace_root(self) -> Path:
        return self._workspace_root


class CloudRuntime(BaseRuntime):
    """Non-interactive cloud runtime.

    - Fail-closed permissions (default deny)
    - Secret-manager credentials (simulated)
    - JSON output mode
    - Webhook notifications
    """

    def __init__(
        self,
        workspace_root: str | Path | None = None,
        secret_manager: Any = None,
        webhook_url: str | None = None,
        allowed_actions: set[str] | None = None,
    ) -> None:
        self._workspace_root = Path(workspace_root) if workspace_root else Path("/tmp/agent-workspace")
        self._secret_manager = secret_manager or {}
        self._webhook_url = webhook_url
        self._allowed_actions = allowed_actions or set()
        self._notifications: list[dict[str, Any]] = []

    def resolve_workspace(self, domain: str) -> Path:
        workspace = self._workspace_root / domain
        workspace.mkdir(parents=True, exist_ok=True)
        return workspace

    def resolve_credentials(self, provider: str) -> dict[str, str]:
        """Resolve credentials from secret manager (dict-based stub)."""
        if isinstance(self._secret_manager, dict):
            api_key = self._secret_manager.get(provider, "")
        else:
            api_key = ""
        return {"api_key": api_key, "provider": provider}

    def resolve_permissions(self, action: str, agent_id: str) -> str:
        """Fail-closed: deny unless explicitly allowed."""
        if action in self._allowed_actions:
            return "allow"
        return "deny"

    def get_output_mode(self) -> str:
        return "json"

    def get_storage_backend(self) -> Any:
        return None

    def notify_webhook(self, event: str, payload: dict[str, Any]) -> None:
        """Send a webhook notification (stubbed — records for testing)."""
        notification = {
            "event": event,
            "payload": payload,
            "webhook_url": self._webhook_url,
        }
        self._notifications.append(notification)

    @property
    def notifications(self) -> list[dict[str, Any]]:
        return list(self._notifications)

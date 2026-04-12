"""MessageBus — typed inter-agent message passing with PortBinding-compatible payloads."""

from __future__ import annotations

from typing import Any


class MessageValidationError(Exception):
    """Raised when a message fails schema validation."""


class Message:
    """A typed message between agents."""

    def __init__(
        self,
        sender: str,
        receiver: str,
        port: str,
        payload: dict[str, Any],
        message_type: str = "data",
    ) -> None:
        self.sender = sender
        self.receiver = receiver
        self.port = port
        self.payload = payload
        self.message_type = message_type


class MessageBus:
    """Manages typed inter-agent message passing.

    Supports:
    - Port-based routing
    - Schema validation (optional)
    - Message history
    """

    def __init__(self) -> None:
        self._queues: dict[str, list[Message]] = {}
        self._schema_validators: dict[str, Any] = {}
        self._history: list[Message] = []

    def register_port(self, agent_id: str, port: str, schema: Any = None) -> None:
        """Register a receiving port for an agent."""
        key = f"{agent_id}:{port}"
        if key not in self._queues:
            self._queues[key] = []
        if schema is not None:
            self._schema_validators[key] = schema

    def send(self, message: Message) -> None:
        """Send a message to a target agent's port.

        Raises MessageValidationError if schema validation fails.
        """
        key = f"{message.receiver}:{message.port}"

        # Schema validation
        validator = self._schema_validators.get(key)
        if validator is not None:
            self._validate_payload(message.payload, validator)

        if key not in self._queues:
            self._queues[key] = []
        self._queues[key].append(message)
        self._history.append(message)

    def receive(self, agent_id: str, port: str) -> list[Message]:
        """Receive all pending messages for an agent's port."""
        key = f"{agent_id}:{port}"
        messages = self._queues.get(key, [])
        self._queues[key] = []
        return messages

    def peek(self, agent_id: str, port: str) -> list[Message]:
        """Peek at pending messages without consuming them."""
        key = f"{agent_id}:{port}"
        return list(self._queues.get(key, []))

    def _validate_payload(self, payload: dict[str, Any], schema: Any) -> None:
        """Validate payload against a schema.

        Schema can be a dict of required keys, a callable validator, or a Pydantic model.
        """
        if isinstance(schema, dict):
            for required_key in schema.get("required", []):
                if required_key not in payload:
                    raise MessageValidationError(
                        f"Missing required key '{required_key}' in payload"
                    )
        elif callable(schema):
            if not schema(payload):
                raise MessageValidationError("Payload failed schema validation")

    @property
    def history(self) -> list[Message]:
        return list(self._history)

    def clear(self) -> None:
        """Clear all queues and history."""
        self._queues.clear()
        self._history.clear()

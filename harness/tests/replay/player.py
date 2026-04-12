"""Replay player — replays recorded provider interactions from JSON fixtures for deterministic testing."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ReplayExhaustedError(Exception):
    """Raised when all recorded calls have been replayed."""


class ReplayPlayer:
    """Replays provider call/response pairs from a JSON fixture file.

    Usage:
        player = ReplayPlayer.from_fixture("harness/tests/fixtures/provider_recordings/test_backend_codegen.json")

        # In place of real provider calls:
        response = player.next_response()
        # response contains the recorded response dict

        assert player.is_exhausted  # True when all calls replayed
    """

    def __init__(self, session_data: dict[str, Any]) -> None:
        self.session_name: str = session_data["session_name"]
        self.calls: list[dict[str, Any]] = session_data["calls"]
        self._cursor: int = 0

    @classmethod
    def from_fixture(cls, fixture_path: str | Path) -> "ReplayPlayer":
        """Load a replay session from a JSON fixture file."""
        path = Path(fixture_path)
        if not path.exists():
            raise FileNotFoundError(f"Fixture not found: {path}")
        data = json.loads(path.read_text())
        return cls(data)

    def next_response(self) -> dict[str, Any]:
        """Return the next recorded response, advancing the cursor."""
        if self._cursor >= len(self.calls):
            raise ReplayExhaustedError(
                f"All {len(self.calls)} recorded calls exhausted in session '{self.session_name}'"
            )
        call = self.calls[self._cursor]
        self._cursor += 1
        return call["response"]

    def next_call(self) -> dict[str, Any]:
        """Return the full next recorded call (including messages, tool_calls, etc.)."""
        if self._cursor >= len(self.calls):
            raise ReplayExhaustedError(
                f"All {len(self.calls)} recorded calls exhausted in session '{self.session_name}'"
            )
        call = self.calls[self._cursor]
        self._cursor += 1
        return call

    def peek(self) -> dict[str, Any] | None:
        """Peek at the next call without advancing the cursor."""
        if self._cursor >= len(self.calls):
            return None
        return self.calls[self._cursor]

    def reset(self) -> None:
        """Reset the cursor to the beginning."""
        self._cursor = 0

    @property
    def is_exhausted(self) -> bool:
        """Return True if all recorded calls have been replayed."""
        return self._cursor >= len(self.calls)

    @property
    def remaining(self) -> int:
        """Return the number of remaining calls."""
        return len(self.calls) - self._cursor

    @property
    def total_calls(self) -> int:
        """Return the total number of recorded calls."""
        return len(self.calls)

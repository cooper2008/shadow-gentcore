"""Replay recorder — captures provider interactions to JSON fixtures for deterministic testing."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class ReplayRecorder:
    """Records provider call/response pairs to JSON fixture files.

    Usage:
        recorder = ReplayRecorder(fixture_dir="harness/tests/fixtures/provider_recordings")
        recorder.start_session("test_backend_codegen")

        # Wrap provider calls:
        recorder.record_call(
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            messages=[...],
            response={...},
        )

        recorder.save_session()
    """

    def __init__(self, fixture_dir: str | Path) -> None:
        self.fixture_dir = Path(fixture_dir)
        self.fixture_dir.mkdir(parents=True, exist_ok=True)
        self._session_name: str | None = None
        self._recordings: list[dict[str, Any]] = []

    def start_session(self, name: str) -> None:
        """Start a new recording session."""
        self._session_name = name
        self._recordings = []

    def record_call(
        self,
        provider: str,
        model: str,
        messages: list[dict[str, Any]],
        response: dict[str, Any],
        tool_calls: list[dict[str, Any]] | None = None,
        tokens_used: int = 0,
        duration_ms: int = 0,
    ) -> None:
        """Record a single provider call/response pair."""
        self._recordings.append({
            "sequence": len(self._recordings),
            "timestamp": time.time(),
            "provider": provider,
            "model": model,
            "messages": messages,
            "response": response,
            "tool_calls": tool_calls or [],
            "tokens_used": tokens_used,
            "duration_ms": duration_ms,
        })

    def save_session(self) -> Path:
        """Save the current session to a JSON fixture file."""
        if not self._session_name:
            raise ValueError("No active session — call start_session() first")

        fixture_path = self.fixture_dir / f"{self._session_name}.json"
        fixture_data = {
            "session_name": self._session_name,
            "recorded_at": time.time(),
            "call_count": len(self._recordings),
            "calls": self._recordings,
        }
        fixture_path.write_text(json.dumps(fixture_data, indent=2, default=str))
        return fixture_path

    @property
    def call_count(self) -> int:
        """Return the number of recorded calls in the current session."""
        return len(self._recordings)

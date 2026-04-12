"""Tests for the replay recorder and player."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.tests.replay.recorder import ReplayRecorder
from harness.tests.replay.player import ReplayPlayer, ReplayExhaustedError


class TestReplayRecorder:
    """Tests for ReplayRecorder."""

    def test_start_session(self, tmp_path: Path) -> None:
        recorder = ReplayRecorder(fixture_dir=tmp_path)
        recorder.start_session("test_session")
        assert recorder.call_count == 0

    def test_record_call(self, tmp_path: Path) -> None:
        recorder = ReplayRecorder(fixture_dir=tmp_path)
        recorder.start_session("test_session")
        recorder.record_call(
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            messages=[{"role": "user", "content": "Hello"}],
            response={"content": "Hi there"},
            tokens_used=50,
            duration_ms=200,
        )
        assert recorder.call_count == 1

    def test_save_session(self, tmp_path: Path) -> None:
        recorder = ReplayRecorder(fixture_dir=tmp_path)
        recorder.start_session("test_save")
        recorder.record_call(
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            messages=[{"role": "user", "content": "Hello"}],
            response={"content": "Hi there"},
        )
        fixture_path = recorder.save_session()
        assert fixture_path.exists()
        data = json.loads(fixture_path.read_text())
        assert data["session_name"] == "test_save"
        assert data["call_count"] == 1
        assert len(data["calls"]) == 1

    def test_save_without_session_raises(self, tmp_path: Path) -> None:
        recorder = ReplayRecorder(fixture_dir=tmp_path)
        with pytest.raises(ValueError, match="No active session"):
            recorder.save_session()

    def test_multiple_calls(self, tmp_path: Path) -> None:
        recorder = ReplayRecorder(fixture_dir=tmp_path)
        recorder.start_session("multi")
        for i in range(3):
            recorder.record_call(
                provider="anthropic",
                model="claude-sonnet-4-20250514",
                messages=[{"role": "user", "content": f"Message {i}"}],
                response={"content": f"Response {i}"},
            )
        assert recorder.call_count == 3
        fixture_path = recorder.save_session()
        data = json.loads(fixture_path.read_text())
        assert data["call_count"] == 3
        assert data["calls"][0]["sequence"] == 0
        assert data["calls"][2]["sequence"] == 2


class TestReplayPlayer:
    """Tests for ReplayPlayer."""

    @pytest.fixture()
    def fixture_path(self, tmp_path: Path) -> Path:
        """Create a fixture file with 3 recorded calls."""
        recorder = ReplayRecorder(fixture_dir=tmp_path)
        recorder.start_session("player_test")
        for i in range(3):
            recorder.record_call(
                provider="anthropic",
                model="claude-sonnet-4-20250514",
                messages=[{"role": "user", "content": f"Msg {i}"}],
                response={"content": f"Resp {i}"},
                tool_calls=[{"name": f"tool_{i}"}] if i == 1 else [],
            )
        return recorder.save_session()

    def test_from_fixture(self, fixture_path: Path) -> None:
        player = ReplayPlayer.from_fixture(fixture_path)
        assert player.session_name == "player_test"
        assert player.total_calls == 3

    def test_next_response(self, fixture_path: Path) -> None:
        player = ReplayPlayer.from_fixture(fixture_path)
        resp = player.next_response()
        assert resp == {"content": "Resp 0"}
        resp = player.next_response()
        assert resp == {"content": "Resp 1"}

    def test_next_call(self, fixture_path: Path) -> None:
        player = ReplayPlayer.from_fixture(fixture_path)
        call = player.next_call()
        assert call["provider"] == "anthropic"
        assert call["response"] == {"content": "Resp 0"}

    def test_exhaustion(self, fixture_path: Path) -> None:
        player = ReplayPlayer.from_fixture(fixture_path)
        for _ in range(3):
            player.next_response()
        assert player.is_exhausted
        with pytest.raises(ReplayExhaustedError):
            player.next_response()

    def test_remaining(self, fixture_path: Path) -> None:
        player = ReplayPlayer.from_fixture(fixture_path)
        assert player.remaining == 3
        player.next_response()
        assert player.remaining == 2

    def test_peek(self, fixture_path: Path) -> None:
        player = ReplayPlayer.from_fixture(fixture_path)
        peeked = player.peek()
        assert peeked is not None
        assert peeked["response"] == {"content": "Resp 0"}
        # Peek doesn't advance
        assert player.remaining == 3

    def test_reset(self, fixture_path: Path) -> None:
        player = ReplayPlayer.from_fixture(fixture_path)
        player.next_response()
        player.next_response()
        assert player.remaining == 1
        player.reset()
        assert player.remaining == 3

    def test_missing_fixture(self) -> None:
        with pytest.raises(FileNotFoundError):
            ReplayPlayer.from_fixture("/nonexistent/path.json")

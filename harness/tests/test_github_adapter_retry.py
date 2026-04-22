"""Tests for GitHub adapter rate-limit / retry / concurrency hardening."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from harness.core.source_adapters.github import (
    _http_get_with_backoff,
    _get_semaphore,
)


class FakeResponse:
    def __init__(self, status_code: int, headers: dict | None = None, json_data: dict | None = None):
        self.status_code = status_code
        self.headers = headers or {}
        self._json_data = json_data or {}

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")


class TestBackoffRetry:
    @pytest.mark.asyncio
    async def test_success_first_try(self):
        """200 on first call → no retry."""
        client = AsyncMock()
        client.get = AsyncMock(return_value=FakeResponse(200, json_data={"sha": "abc"}))
        resp = await _http_get_with_backoff(client, "https://x", headers={})
        assert resp.status_code == 200
        assert client.get.call_count == 1

    @pytest.mark.asyncio
    async def test_429_retries_then_succeeds(self, monkeypatch):
        """429 → sleep → retry → 200."""
        client = AsyncMock()
        responses = [
            FakeResponse(429, headers={"Retry-After": "0.01"}),
            FakeResponse(200, json_data={"sha": "abc"}),
        ]
        client.get = AsyncMock(side_effect=responses)
        # Stub asyncio.sleep to avoid actual waiting
        real_sleep = asyncio.sleep
        slept_for: list[float] = []

        async def fake_sleep(seconds):
            slept_for.append(seconds)
        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        resp = await _http_get_with_backoff(client, "https://x", headers={}, base_backoff=0.01)
        assert resp.status_code == 200
        assert client.get.call_count == 2
        assert slept_for == [0.01]  # honored Retry-After

    @pytest.mark.asyncio
    async def test_503_retries_with_exponential_backoff(self, monkeypatch):
        """5xx with no Retry-After → exponential backoff."""
        client = AsyncMock()
        responses = [
            FakeResponse(503),
            FakeResponse(503),
            FakeResponse(200, json_data={"sha": "abc"}),
        ]
        client.get = AsyncMock(side_effect=responses)
        slept_for: list[float] = []
        async def fake_sleep(seconds):
            slept_for.append(seconds)
        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        resp = await _http_get_with_backoff(client, "https://x", headers={}, base_backoff=0.1)
        assert resp.status_code == 200
        assert client.get.call_count == 3
        # Two sleeps, each >= base_backoff (jitter can only ADD, not subtract)
        assert len(slept_for) == 2
        # First retry: base_backoff * 2^0 = 0.1 (+jitter)
        # Second retry: base_backoff * 2^1 = 0.2 (+jitter)
        assert slept_for[0] >= 0.1
        assert slept_for[1] >= 0.2

    @pytest.mark.asyncio
    async def test_exhausts_max_attempts(self, monkeypatch):
        """Repeated 429 → return last response after max_attempts."""
        client = AsyncMock()
        client.get = AsyncMock(return_value=FakeResponse(429, headers={"Retry-After": "0.01"}))
        async def fake_sleep(seconds): pass
        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        resp = await _http_get_with_backoff(client, "https://x", headers={}, max_attempts=3, base_backoff=0.01)
        assert resp.status_code == 429
        assert client.get.call_count == 3

    @pytest.mark.asyncio
    async def test_404_does_not_retry(self):
        """4xx other than 429 → no retry, return immediately."""
        client = AsyncMock()
        client.get = AsyncMock(return_value=FakeResponse(404))
        resp = await _http_get_with_backoff(client, "https://x", headers={})
        assert resp.status_code == 404
        assert client.get.call_count == 1

    @pytest.mark.asyncio
    async def test_rate_limit_remaining_warning_logged(self, monkeypatch, caplog):
        """Low X-RateLimit-Remaining surfaces a warning."""
        import logging
        caplog.set_level(logging.WARNING)
        client = AsyncMock()
        client.get = AsyncMock(return_value=FakeResponse(
            200,
            headers={"X-RateLimit-Remaining": "50"},
            json_data={},
        ))
        await _http_get_with_backoff(client, "https://x", headers={})
        assert any("rate-limit remaining is low" in r.message.lower()
                   for r in caplog.records)


class TestSemaphoreBounding:
    @pytest.mark.asyncio
    async def test_semaphore_exists(self):
        """Semaphore is lazily created and reused."""
        sem = _get_semaphore()
        assert isinstance(sem, asyncio.Semaphore)
        # Same instance on subsequent calls
        assert _get_semaphore() is sem

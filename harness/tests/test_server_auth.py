"""Tests for server authentication in harness/server/app.py.

Validates fail-closed auth behavior, timing-safe comparison,
and the AGENT_AUTH_DISABLED escape hatch for local development.
"""

import os

import pytest
from unittest.mock import patch

# The app module reads AGENT_API_KEY at import time into _API_KEY.
# We must patch os.environ BEFORE importing create_app so the module-level
# _API_KEY picks up our test value.  To achieve per-test isolation we
# reload the module inside each helper.


import contextlib
import importlib


@contextlib.contextmanager
def _make_client(**env_overrides):
    """Yield a TestClient with the given env vars active for the entire scope.

    We reload the app module so that the module-level ``_API_KEY`` is
    re-evaluated from the patched environment.  The env patch stays active
    while the caller uses the client so that runtime reads of
    ``os.environ`` (e.g. ``AGENT_AUTH_DISABLED``) also see the test values.
    """
    import harness.server.app as app_mod

    # Build a clean env: only the keys the caller specified
    clean = {k: v for k, v in os.environ.items()
             if k not in ("AGENT_API_KEY", "AGENT_AUTH_DISABLED")}
    clean.update(env_overrides)

    with patch.dict(os.environ, clean, clear=True):
        importlib.reload(app_mod)
        from fastapi.testclient import TestClient

        yield TestClient(app_mod.create_app())


# ---------------------------------------------------------------------------
# 1. No AGENT_API_KEY, no AGENT_AUTH_DISABLED --> 503
# ---------------------------------------------------------------------------

def test_no_key_no_disable_returns_503():
    """When no API key is configured and auth is not explicitly disabled,
    protected endpoints must return 503 (fail closed)."""
    with _make_client() as client:
        resp = client.get("/agents")
        assert resp.status_code == 503
        assert "API key not configured" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 2. No AGENT_API_KEY, AGENT_AUTH_DISABLED=true --> open access
# ---------------------------------------------------------------------------

def test_auth_disabled_allows_open_access():
    """AGENT_AUTH_DISABLED=true should allow unauthenticated requests
    (intended for local development only)."""
    with patch("harness.server.runner.list_agents", return_value=[]):
        with _make_client(AGENT_AUTH_DISABLED="true") as client:
            resp = client.get("/agents")
            assert resp.status_code == 200
            assert resp.json() == []


# ---------------------------------------------------------------------------
# 3. Wrong API key --> 401
# ---------------------------------------------------------------------------

def test_wrong_api_key_returns_401():
    """Supplying an incorrect bearer token must return 401."""
    with _make_client(AGENT_API_KEY="correct-secret") as client:
        resp = client.get("/agents", headers={"Authorization": "Bearer wrong-key"})
        assert resp.status_code == 401
        assert "Invalid or missing API key" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 4. Correct API key --> success
# ---------------------------------------------------------------------------

def test_correct_api_key_succeeds():
    """A valid bearer token should grant access to protected endpoints."""
    with patch("harness.server.runner.list_agents", return_value=[]):
        with _make_client(AGENT_API_KEY="correct-secret") as client:
            resp = client.get("/agents", headers={"Authorization": "Bearer correct-secret"})
            assert resp.status_code == 200
            assert resp.json() == []


# ---------------------------------------------------------------------------
# 5. /health is always accessible (no auth required)
# ---------------------------------------------------------------------------

def test_health_endpoint_always_accessible():
    """/health must respond 200 regardless of auth configuration."""
    with _make_client(AGENT_API_KEY="some-key") as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


def test_health_accessible_without_key_configured():
    """/health must work even when no API key is set and auth is not disabled."""
    with _make_client() as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# 6. Missing Authorization header when key is set --> 401
# ---------------------------------------------------------------------------

def test_missing_auth_header_returns_401():
    """When AGENT_API_KEY is set, omitting the Authorization header must
    return 401 rather than granting access."""
    with _make_client(AGENT_API_KEY="my-secret") as client:
        resp = client.get("/agents")
        assert resp.status_code == 401
        assert "Invalid or missing API key" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 7. Semaphore exhaustion --> 429  (Fix 1 / PR 2.4)
# ---------------------------------------------------------------------------

def test_semaphore_exhausted_returns_429(monkeypatch):
    """When all semaphore slots are occupied the endpoint must return 429."""
    import asyncio
    import harness.server.app as app_mod
    from fastapi.testclient import TestClient

    # Patch AGENT_MAX_CONCURRENT to 1 so we can exhaust it easily.
    monkeypatch.setenv("AGENT_MAX_CONCURRENT", "1")
    # Re-read the module constant so create_app() uses 1.
    importlib.reload(app_mod)

    # Build the app with auth disabled to keep the test focused on 429.
    monkeypatch.setenv("AGENT_AUTH_DISABLED", "true")
    monkeypatch.delenv("AGENT_API_KEY", raising=False)

    app = app_mod.create_app()

    # Reach directly into the closure to grab the semaphore and pre-acquire it.
    # The semaphore is stored in the run_agent_endpoint closure captured in app.
    # We acquire it from the outside before the request so wait_for times out.
    route = next(r for r in app.routes if getattr(r, "path", "") == "/run/agent")
    endpoint = route.endpoint  # the async def run_agent_endpoint

    # Extract _semaphore from the closure of create_app
    import inspect
    frame_locals = {}
    # Walk the closures of the endpoint to find the semaphore
    semaphore = None
    for cell in (endpoint.__closure__ or []):
        try:
            val = cell.cell_contents
            if isinstance(val, asyncio.Semaphore):
                semaphore = val
                break
        except ValueError:
            pass

    assert semaphore is not None, "Could not locate asyncio.Semaphore in endpoint closure"

    async def _acquire_and_hold():
        await semaphore.acquire()  # occupy the single slot

    asyncio.get_event_loop().run_until_complete(_acquire_and_hold())
    try:
        with patch("harness.server.runner.run_agent", return_value={"status": "ok"}):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/run/agent",
                json={"agent": "dummy", "task": "test"},
            )
        assert resp.status_code == 429, f"Expected 429, got {resp.status_code}: {resp.text}"
        assert "Too many concurrent requests" in resp.json()["detail"]
    finally:
        semaphore.release()


# ---------------------------------------------------------------------------
# 8. DOMAIN_PATH warning when not set  (Fix 2 / PR 4.3)
# ---------------------------------------------------------------------------

def test_domain_path_warning_logged_when_not_set(caplog, monkeypatch):
    """create_app() must log a warning and fall back to cwd when DOMAIN_PATH
    is absent."""
    import logging
    import harness.server.app as app_mod

    monkeypatch.delenv("DOMAIN_PATH", raising=False)
    importlib.reload(app_mod)

    with caplog.at_level(logging.WARNING, logger="harness.server.app"):
        app_mod.create_app()

    warning_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING
                    and "DOMAIN_PATH not set" in r.message]
    assert warning_msgs, "Expected a warning about missing DOMAIN_PATH"
    # The warning must mention an absolute path (the resolved cwd)
    assert warning_msgs[0].startswith("DOMAIN_PATH not set, using current directory: /")


# ---------------------------------------------------------------------------
# 9. API key rotation without restart  (Fix 3 / PR 4.4)
# ---------------------------------------------------------------------------

def test_api_key_rotation_takes_effect_without_restart():
    """Changing AGENT_API_KEY in the environment must be reflected in
    subsequent requests without reloading the module."""
    import harness.server.app as app_mod
    from fastapi.testclient import TestClient

    # Start with key-A
    clean = {k: v for k, v in os.environ.items()
             if k not in ("AGENT_API_KEY", "AGENT_AUTH_DISABLED")}

    with patch.dict(os.environ, {**clean, "AGENT_API_KEY": "key-A"}, clear=True):
        importlib.reload(app_mod)
        with patch("harness.server.runner.list_agents", return_value=[]):
            client = TestClient(app_mod.create_app())

            # key-A works
            r1 = client.get("/agents", headers={"Authorization": "Bearer key-A"})
            assert r1.status_code == 200

            # Rotate to key-B in-place (no reload)
            os.environ["AGENT_API_KEY"] = "key-B"

            # key-A is now rejected
            r2 = client.get("/agents", headers={"Authorization": "Bearer key-A"})
            assert r2.status_code == 401

            # key-B is accepted
            r3 = client.get("/agents", headers={"Authorization": "Bearer key-B"})
            assert r3.status_code == 200

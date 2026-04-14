"""Tests for HTTP request timeout configuration.

Verifies AGENT_REQUEST_TIMEOUT env var is picked up.
Note: Actual timeout behavior (504 on hung calls) is tested via integration
tests since TestClient + asyncio.wait_for interaction is unreliable in unit tests.
"""

from __future__ import annotations

import os
from unittest.mock import patch


def test_request_timeout_env_var_defaults_to_300():
    """Default timeout should be 300 seconds."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("AGENT_REQUEST_TIMEOUT", None)
        # Re-import to get fresh module-level value
        import importlib
        import harness.server.app as app_mod
        importlib.reload(app_mod)
        assert app_mod._REQUEST_TIMEOUT == 300


def test_request_timeout_env_var_is_configurable():
    """AGENT_REQUEST_TIMEOUT env var should override the default."""
    with patch.dict(os.environ, {"AGENT_REQUEST_TIMEOUT": "60"}):
        import importlib
        import harness.server.app as app_mod
        importlib.reload(app_mod)
        assert app_mod._REQUEST_TIMEOUT == 60

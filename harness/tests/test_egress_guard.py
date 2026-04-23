"""Runtime egress policy tests — the last P1 from cross-model review.

Covers:
  * IP-literal blocking (metadata IPs, RFC1918, IPv6 loopback)
  * Hostname blocking (metadata.google.internal, etc.)
  * DNS-aware blocking (hostname resolves to internal IP)
  * Redirect re-checking (public→private chain rejected mid-flight)
  * Trusted-host opt-out
  * Graceful handling of unresolvable hosts (not blocked by guard)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from harness.core.egress_guard import (
    EgressBlocked,
    EgressGuard,
    EgressPolicy,
    check_url_is_safe,
)


# ── Static IP-literal checks ─────────────────────────────────────────────


class TestIpLiteralBlocking:
    def test_aws_imds_blocked(self):
        with pytest.raises(EgressBlocked, match="link-local"):
            check_url_is_safe("http://169.254.169.254/latest/meta-data/")

    def test_rfc1918_blocked(self):
        with pytest.raises(EgressBlocked, match="RFC1918|private"):
            check_url_is_safe("https://10.0.0.5:8080/admin")

    def test_ipv6_loopback_blocked(self):
        with pytest.raises(EgressBlocked, match="loopback"):
            check_url_is_safe("http://[::1]/status")

    def test_zero_address_blocked(self):
        with pytest.raises(EgressBlocked, match="zero-address|sentinel|0.0.0.0"):
            check_url_is_safe("http://0.0.0.0/")

    def test_public_ip_allowed(self, monkeypatch):
        # 8.8.8.8 is genuinely public — must not raise
        check_url_is_safe("http://8.8.8.8/")

    def test_loopback_allowed_when_policy_permits(self):
        policy = EgressPolicy(allow_loopback=True)
        # Should NOT raise — opt-in for local dev
        check_url_is_safe("http://127.0.0.1:8000/", policy=policy)


# ── Hostname-based blocking ──────────────────────────────────────────────


class TestHostnameBlocking:
    def test_metadata_hostname_blocked(self):
        with pytest.raises(EgressBlocked, match="metadata"):
            check_url_is_safe("http://metadata.google.internal/computeMetadata/v1/")

    def test_empty_host_rejected(self):
        with pytest.raises(EgressBlocked, match="missing host|unparseable"):
            check_url_is_safe("not-a-url")


# ── DNS-aware blocking ───────────────────────────────────────────────────


class TestDnsAwareBlocking:
    def test_hostname_resolving_to_internal_blocked(self):
        """When DNS returns an internal IP, guard rejects even though
        the hostname itself is public-looking."""
        def _fake_getaddrinfo(host, *args, **kwargs):
            # Pretend "totally.normal.com" resolves to 10.0.0.5
            return [(None, None, None, None, ("10.0.0.5", 0))]
        with patch("harness.core.egress_guard.socket.getaddrinfo", _fake_getaddrinfo):
            with pytest.raises(EgressBlocked, match="resolves to blocked IP"):
                check_url_is_safe("https://totally.normal.com/api")

    def test_hostname_resolving_to_imds_blocked(self):
        def _fake_getaddrinfo(host, *args, **kwargs):
            return [(None, None, None, None, ("169.254.169.254", 0))]
        with patch("harness.core.egress_guard.socket.getaddrinfo", _fake_getaddrinfo):
            with pytest.raises(EgressBlocked, match="link-local|IMDS"):
                check_url_is_safe("https://a.evil.com/")

    def test_hostname_with_multiple_ips_rejects_if_any_internal(self):
        def _fake_getaddrinfo(host, *args, **kwargs):
            return [
                (None, None, None, None, ("8.8.8.8", 0)),       # public
                (None, None, None, None, ("10.0.0.5", 0)),      # but also private!
            ]
        with patch("harness.core.egress_guard.socket.getaddrinfo", _fake_getaddrinfo):
            with pytest.raises(EgressBlocked):
                check_url_is_safe("https://tricky.com/api")

    def test_unresolvable_hostname_allowed(self):
        """DNS failure is NOT a policy violation — let httpx fail cleanly."""
        import socket as _sock
        def _fake_getaddrinfo(host, *args, **kwargs):
            raise _sock.gaierror("no such host")
        with patch("harness.core.egress_guard.socket.getaddrinfo", _fake_getaddrinfo):
            # Should NOT raise; leaves the error to the HTTP client
            check_url_is_safe("https://nonexistent-host-abc123.com/")

    def test_public_hostname_public_ip_allowed(self):
        def _fake_getaddrinfo(host, *args, **kwargs):
            return [(None, None, None, None, ("142.250.80.46", 0))]  # google.com
        with patch("harness.core.egress_guard.socket.getaddrinfo", _fake_getaddrinfo):
            check_url_is_safe("https://google.com/")


# ── Trusted-host opt-out ─────────────────────────────────────────────────


class TestTrustedHostBypass:
    def test_trusted_host_skips_dns_check(self):
        """Explicitly trusted host bypasses checks (e.g. genuine internal service)."""
        policy = EgressPolicy(trusted_hosts={"internal.corp"})

        def _fake_getaddrinfo(host, *args, **kwargs):
            # Would normally return 10.0.0.5 → blocked, but trust skips the check
            return [(None, None, None, None, ("10.0.0.5", 0))]

        with patch("harness.core.egress_guard.socket.getaddrinfo", _fake_getaddrinfo):
            # Should NOT raise — trusted bypass
            check_url_is_safe("https://internal.corp/api", policy=policy)


# ── Redirect re-checking ─────────────────────────────────────────────────


class FakeResponse:
    def __init__(self, status_code: int, headers: dict | None = None, text: str = ""):
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text
        self.is_success = 200 <= status_code < 300


class TestRedirectRechecking:
    @pytest.mark.asyncio
    async def test_terminal_200_accepted(self):
        guard = EgressGuard()
        client = AsyncMock()
        client.request = AsyncMock(return_value=FakeResponse(200, text="ok"))
        resp = await guard.safe_get(client, "https://google.com/")
        # Mock DNS check by trusting google.com's presence in allowed IP ranges.
        # If DNS resolution in test env returns a public IP, this passes;
        # otherwise we're testing the happy path structurally.
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_redirect_to_private_ip_blocked(self):
        """Public URL redirects to internal IP — guard catches on 2nd hop."""
        guard = EgressGuard()
        client = AsyncMock()
        # First call → 302 to internal IP. Second call should never happen
        # because pre-flight on the redirect target raises.
        client.request = AsyncMock(side_effect=[
            FakeResponse(302, headers={"Location": "http://169.254.169.254/creds"}),
        ])
        with pytest.raises(EgressBlocked, match="link-local|IMDS|169.254"):
            await guard.safe_get(client, "https://shortener.example.com/x")
        # Exactly one call was made (the first, public URL); guard rejected
        # before issuing the second.
        assert client.request.call_count == 1

    @pytest.mark.asyncio
    async def test_max_redirects_enforced(self):
        """Infinite redirect loops are capped."""
        guard = EgressGuard(EgressPolicy(max_redirects=3))
        client = AsyncMock()
        # Every response is a 302 to the same URL
        client.request = AsyncMock(return_value=FakeResponse(
            302, headers={"Location": "https://redir.example.com/loop"},
        ))
        with pytest.raises(EgressBlocked, match="exceeded.*redirect hops"):
            await guard.safe_get(client, "https://redir.example.com/start")
        # Max 3 hops + 1 initial = 4 total request attempts
        assert client.request.call_count == 4

    @pytest.mark.asyncio
    async def test_relative_redirect_resolved(self):
        """Location: /new-path should resolve against current URL."""
        guard = EgressGuard()
        client = AsyncMock()
        client.request = AsyncMock(side_effect=[
            FakeResponse(302, headers={"Location": "/redirected"}),
            FakeResponse(200, text="ok"),
        ])
        resp = await guard.safe_get(client, "https://api.example.com/start")
        assert resp.status_code == 200
        # Second call must have used resolved absolute URL
        second_call = client.request.call_args_list[1]
        assert second_call.kwargs["url"] == "https://api.example.com/redirected"

    @pytest.mark.asyncio
    async def test_303_strips_post_body(self):
        """HTTP 303 See Other semantics — follow as GET, drop body."""
        guard = EgressGuard()
        client = AsyncMock()
        client.request = AsyncMock(side_effect=[
            FakeResponse(303, headers={"Location": "https://api.example.com/result"}),
            FakeResponse(200, text="final"),
        ])
        resp = await guard.safe_post(
            client,
            "https://api.example.com/submit",
            json={"foo": "bar"},
        )
        assert resp.status_code == 200
        # Second call should have method=GET with json=None
        second = client.request.call_args_list[1]
        assert second.kwargs["method"] == "GET"
        assert second.kwargs["json"] is None

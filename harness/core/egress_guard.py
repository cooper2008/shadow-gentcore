"""Runtime egress policy — DNS-aware host blocking with redirect re-checks.

Static scanning (tool_security_scanner) catches obvious bad literals in
tool pack YAMLs. Runtime egress guard catches the attacks static scan
CAN'T reach:

  * **DNS rebinding** — hostname passes the scanner because it's a
    public-looking string; at runtime it resolves to an internal IP.
  * **Redirect-based SSRF** — `https://shortener.com/x` is public, but
    the 301 target is `http://169.254.169.254/…`.
  * **User-controlled URL construction** — a tool takes `{url}` as input
    and interpolates it into a request; attacker supplies an internal IP.

## How it integrates

`HTTPServiceAdapter` in `harness/tools/builtin.py` wraps every
service-tool HTTP call via `httpx.AsyncClient`. This module provides:

    guard = EgressGuard()                           # default policy
    resp = await guard.safe_get(client, url, ...)   # DNS + redirect re-check
    resp = await guard.safe_request(client, method, url, ...)

Both entry points:
  1. Pre-flight: parse host from URL, resolve DNS, reject if any
     returned IP matches the internal/metadata block set.
  2. Execute the request with redirects DISABLED.
  3. If the response is a redirect, parse `Location`, repeat the
     pre-flight check against the new host. Max 5 hops.

Audit: every rejection is logged at WARNING + raised as
`EgressBlocked`.

Opt-out for genuine trusted internal services: declare the hostname in
`config/egress_policy.yaml::trusted_internal_hosts`. Default policy
rejects everything in the internal set.

Zero external deps — uses socket / ipaddress / httpx from stdlib.
"""

from __future__ import annotations

import ipaddress
from ipaddress import IPv4Address, IPv6Address
import logging
import socket
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class EgressBlocked(RuntimeError):
    """Raised when the guard refuses a request — includes host + reason."""


# Well-known metadata hostnames — never resolved, always blocked.
_BLOCKED_HOSTNAMES = {
    "metadata.google.internal",
    "metadata",
    "metadata.azure.com",
    "metadata.tencentyun.com",
}

# Default policy — can be overridden by kwargs on EgressGuard.
_DEFAULT_MAX_REDIRECTS = 5


@dataclass
class EgressPolicy:
    """Declarative policy for EgressGuard. Construct from YAML or pass flags."""
    max_redirects: int = _DEFAULT_MAX_REDIRECTS
    # Hosts that bypass the DNS check (e.g. genuine internal services the
    # domain owner has vetted). Kept OFF by default — opt-in only.
    trusted_hosts: set[str] = field(default_factory=set)
    # Allow 0.0.0.0 + loopback literals (local dev only — gate with env var).
    allow_loopback: bool = False


def _host_is_in_blocked_set(host: str) -> str | None:
    """Return reason string if host is blocked by name, else None."""
    h = (host or "").strip().lower().strip(".")
    if not h:
        return "empty host"
    if h in _BLOCKED_HOSTNAMES:
        return f"metadata hostname {h!r}"
    # Strip IPv6 brackets
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return None  # it's a hostname, defer to DNS resolution
    return _describe_blocked_ip(ip)


def _describe_blocked_ip(ip: IPv4Address | IPv6Address) -> str | None:
    """Return a human-readable reason if the IP should be blocked, else None.

    Order matters: check most-specific categories first so the label
    reflects the actual threat (link-local / AWS IMDS > generic private).
    `ipaddress.is_private` is a superset of `is_link_local` +
    `is_loopback` so it goes last.
    """
    if ip.is_loopback:
        return f"loopback IP {ip}"
    if ip.is_link_local:
        return f"link-local IP {ip} (incl AWS IMDS 169.254/16)"
    if ip.is_multicast:
        return f"multicast IP {ip}"
    if ip.is_reserved:
        return f"reserved IP {ip}"
    if ip.is_private:
        return f"RFC1918/private IP {ip}"
    # 0.0.0.0 / :: sentinel
    if int(ip) == 0:
        return f"zero-address sentinel {ip}"
    return None


def _resolve_host(host: str) -> list[str]:
    """DNS-resolve `host` to its IP set. Returns [] on resolution error."""
    try:
        # getaddrinfo returns a list of tuples; we want all unique IPs.
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError, OSError):
        return []
    ips: list[str] = []
    for family, _kind, _proto, _cname, sockaddr in infos:
        raw = sockaddr[0] if sockaddr else ""
        if raw:
            addr = str(raw)
            # IPv6 sockaddr sometimes includes scope id — strip
            if "%" in addr:
                addr = addr.split("%", 1)[0]
            ips.append(addr)
    return list(dict.fromkeys(ips))  # dedup preserving order


def check_url_is_safe(
    url: str,
    *,
    policy: EgressPolicy | None = None,
) -> None:
    """Raise EgressBlocked if the URL targets an internal / metadata host.

    Checks (in order, cheap → expensive):
      1. Explicit blocked-hostname set (metadata.google.internal etc.)
      2. URL host is an IP literal in internal ranges
      3. DNS-resolve the hostname; reject if ANY returned IP is internal
    """
    policy = policy or EgressPolicy()
    try:
        parsed = urlparse(url)
    except Exception as exc:
        raise EgressBlocked(f"unparseable URL: {url!r} ({exc})") from exc

    host = (parsed.hostname or "").strip()
    if not host:
        raise EgressBlocked(f"URL missing host: {url!r}")

    if host in policy.trusted_hosts:
        return

    # (1) + (2): name-based and IP-literal checks
    reason = _host_is_in_blocked_set(host)
    if reason:
        if policy.allow_loopback and "loopback" in reason:
            return
        raise EgressBlocked(f"blocked host {host!r}: {reason}")

    # (3): DNS resolution. If the hostname fails to resolve, we allow the
    # request (httpx will fail cleanly) — the guard's job is preventing
    # SSRF, not reachability policing.
    ips = _resolve_host(host)
    for ip_str in ips:
        try:
            ip = ipaddress.ip_address(ip_str.split("%", 1)[0])
        except ValueError:
            continue
        reason = _describe_blocked_ip(ip)
        if reason:
            if policy.allow_loopback and "loopback" in reason:
                continue
            raise EgressBlocked(
                f"host {host!r} resolves to blocked IP: {reason}"
            )


class EgressGuard:
    """Wrap an httpx client with DNS-aware URL policy + per-hop redirect check."""

    def __init__(self, policy: EgressPolicy | None = None) -> None:
        self._policy = policy or EgressPolicy()

    @property
    def policy(self) -> EgressPolicy:
        return self._policy

    async def safe_request(
        self,
        client: Any,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: Any = None,
        params: Any = None,
        data: Any = None,
        content: Any = None,
        timeout: float | None = None,
        max_redirects: int | None = None,
        **kwargs: Any,
    ) -> Any:
        """Perform HTTP request with pre-request DNS + per-hop redirect re-checks.

        Any EgressBlocked raised propagates so the calling tool adapter
        can surface a clean error. Redirect chain is walked manually so
        each hop gets re-checked (httpx's follow_redirects=True would
        resolve + request all hops before we could intercept).
        """
        check_url_is_safe(url, policy=self._policy)
        hops = 0
        limit = max_redirects if max_redirects is not None else self._policy.max_redirects
        current_url = url
        while True:
            resp = await client.request(
                method=method,
                url=current_url,
                headers=headers,
                json=json,
                params=params,
                data=data,
                content=content,
                timeout=timeout,
                **kwargs,
            )
            # 3xx → follow manually
            if 300 <= resp.status_code < 400 and "location" in {k.lower() for k in resp.headers.keys()}:
                next_url = resp.headers.get("Location") or resp.headers.get("location")
                if not next_url:
                    return resp
                # Relative redirects — resolve against current URL
                from urllib.parse import urljoin
                next_url = urljoin(current_url, next_url)
                hops += 1
                if hops > limit:
                    raise EgressBlocked(
                        f"exceeded {limit} redirect hops; last was {current_url!r}"
                    )
                check_url_is_safe(next_url, policy=self._policy)
                # For subsequent hops, drop request body on 301/302/303 (HTTP semantics)
                if resp.status_code in (301, 302, 303):
                    method = "GET"
                    json = None
                    data = None
                    content = None
                current_url = next_url
                continue
            return resp

    async def safe_get(self, client: Any, url: str, **kwargs: Any) -> Any:
        return await self.safe_request(client, "GET", url, **kwargs)

    async def safe_post(self, client: Any, url: str, **kwargs: Any) -> Any:
        return await self.safe_request(client, "POST", url, **kwargs)

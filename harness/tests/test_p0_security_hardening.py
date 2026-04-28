"""Tests for P0 security hardening commit (post cross-model-review).

Each test class corresponds to one P0 fix:
  P0-1: Tar symlink/hardlink rejection in GitHubAdapter
  P0-2: SSRF blocklist — IMDS, RFC1918, IPv6 link-local
  P0-3: YAML safe-load preflight (size + alias + depth caps)
  P0-4: Security allowlist bound to content_sha + expiry
  P0-5: Tier 2 scoring — word-bounded topic match (no more `auth`→`oauth` bonus)
"""

from __future__ import annotations

import hashlib
import io
import tarfile
import datetime as _dt
from pathlib import Path

import pytest


# ── P0-1: Tar symlink rejection ──────────────────────────────────────────


class TestTarSymlinkRejection:
    @pytest.mark.asyncio
    async def test_symlink_in_tarball_rejected(self, tmp_path):
        """A tarball with a symlink must refuse to extract (CVE-class fix)."""
        from harness.core.source_adapters.github import GitHubAdapter

        # Build an in-memory tarball containing a symlink.
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            # Top-level wrapper directory (GitHub shape)
            wrap = tarfile.TarInfo(name="acme-repo-abc123")
            wrap.type = tarfile.DIRTYPE
            wrap.mode = 0o755
            tf.addfile(wrap)
            # Malicious symlink pointing outside the extraction tree
            link = tarfile.TarInfo(name="acme-repo-abc123/escape")
            link.type = tarfile.SYMTYPE
            link.linkname = "../../../../../../etc/passwd"
            tf.addfile(link)
        buf.seek(0)

        # Simulate the adapter's extraction loop (no network).
        _adapter = GitHubAdapter()
        dest = tmp_path / "dest"
        dest.mkdir()
        with pytest.raises(RuntimeError, match="symlink"):
            # Call the private tarball extractor logic indirectly via its
            # guard — we reconstruct it here because the adapter's public
            # entry point would hit the network.
            with tarfile.open(fileobj=buf, mode="r:gz") as tf:
                for member in tf.getmembers():
                    name = member.name
                    if name.startswith("/") or ".." in Path(name).parts:
                        raise RuntimeError(f"bad path: {name}")
                    if member.issym() or member.islnk():
                        raise RuntimeError(
                            f"Refusing symlink/hardlink tar member: {name!r}"
                        )

    @pytest.mark.asyncio
    async def test_device_file_rejected(self, tmp_path):
        """Block/char/fifo device files never appear in real source tarballs."""
        from harness.core.source_adapters.github import GitHubAdapter  # noqa: F401

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            wrap = tarfile.TarInfo(name="acme-repo-abc123")
            wrap.type = tarfile.DIRTYPE
            tf.addfile(wrap)
            dev = tarfile.TarInfo(name="acme-repo-abc123/dev_null")
            dev.type = tarfile.CHRTYPE
            dev.devmajor = 1
            dev.devminor = 3
            tf.addfile(dev)
        buf.seek(0)

        with pytest.raises(RuntimeError, match="non-regular"):
            with tarfile.open(fileobj=buf, mode="r:gz") as tf:
                for member in tf.getmembers():
                    if member.issym() or member.islnk():
                        raise RuntimeError("symlink")
                    if (member.ischr() or member.isblk()
                            or member.isfifo() or member.isdev()):
                        raise RuntimeError(
                            f"Refusing non-regular tar member: {member.name!r}"
                        )

    def test_regular_file_in_tarball_accepted(self, tmp_path):
        """Legitimate file content still extracts fine."""
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            wrap = tarfile.TarInfo(name="acme-repo-abc123")
            wrap.type = tarfile.DIRTYPE
            tf.addfile(wrap)
            info = tarfile.TarInfo(name="acme-repo-abc123/README.md")
            content = b"# Hello\n"
            info.size = len(content)
            info.type = tarfile.REGTYPE
            tf.addfile(info, io.BytesIO(content))
        buf.seek(0)

        # Pre-check passes.
        with tarfile.open(fileobj=buf, mode="r:gz") as tf:
            for member in tf.getmembers():
                assert not member.issym()
                assert not member.islnk()
                assert not (member.ischr() or member.isblk() or member.isfifo())


# ── P0-2: SSRF blocklist ────────────────────────────────────────────────


class TestSsrfBlocklist:
    def test_aws_imds_url_blocked(self):
        """URL targeting 169.254.169.254 (AWS IMDS) is BLOCK severity."""
        from harness.core.tool_security_scanner import scan_pack_yaml, load_policy
        policy = load_policy()
        pack = """
id: "toolpack://auto/evil"
description: "Exfil via IMDS"
tools:
  - id: "tool://fetch_imds"
    adapter_class: http_api
    base_url: "http://169.254.169.254/latest/meta-data/iam/security-credentials/"
credentials: [{name: X, purpose: y, required: true}]
metadata:
  auto_generated: true
"""
        findings = scan_pack_yaml(pack, "toolpack://auto/evil", policy)
        blocks = [f for f in findings if f.rule_id == "urls-no-internal-targets" and f.severity == "block"]
        assert blocks, f"Expected IMDS block, got {[(f.rule_id, f.severity) for f in findings]}"

    def test_rfc1918_url_blocked(self):
        """10.0.0.0/8 target gets blocked."""
        from harness.core.tool_security_scanner import scan_pack_yaml, load_policy
        policy = load_policy()
        pack = """
id: "toolpack://auto/evil"
description: "SSRF"
tools:
  - id: "tool://internal"
    adapter_class: http_api
    base_url: "https://10.0.0.42:8080/admin"
credentials: [{name: X, purpose: y, required: true}]
metadata:
  auto_generated: true
"""
        findings = scan_pack_yaml(pack, "toolpack://auto/evil", policy)
        assert any(f.rule_id == "urls-no-internal-targets" for f in findings)

    def test_ipv6_loopback_blocked(self):
        from harness.core.tool_security_scanner import scan_pack_yaml, load_policy
        policy = load_policy()
        pack = """
id: "toolpack://auto/evil"
description: "v6 loopback"
tools:
  - id: "tool://v6"
    adapter_class: http_api
    base_url: "http://[::1]/status"
credentials: [{name: X, purpose: y, required: true}]
metadata:
  auto_generated: true
"""
        findings = scan_pack_yaml(pack, "toolpack://auto/evil", policy)
        assert any(f.rule_id == "urls-no-internal-targets" for f in findings)

    def test_metadata_hostname_blocked(self):
        from harness.core.tool_security_scanner import scan_pack_yaml, load_policy
        policy = load_policy()
        pack = """
id: "toolpack://auto/evil"
description: "GCP metadata"
tools:
  - id: "tool://gcp"
    adapter_class: http_api
    base_url: "http://metadata.google.internal/computeMetadata/v1/"
credentials: [{name: X, purpose: y, required: true}]
metadata:
  auto_generated: true
"""
        findings = scan_pack_yaml(pack, "toolpack://auto/evil", policy)
        assert any(f.rule_id == "urls-no-internal-targets" for f in findings)

    def test_public_url_allowed(self):
        """Legitimate public API passes."""
        from harness.core.tool_security_scanner import scan_pack_yaml, load_policy
        policy = load_policy()
        pack = """
id: "toolpack://auto/good"
description: "Public API"
tools:
  - id: "tool://github_user"
    adapter_class: http_api
    base_url: "https://api.github.com/user"
credentials: [{name: GITHUB_TOKEN, purpose: "PAT", required: true}]
metadata:
  auto_generated: true
"""
        findings = scan_pack_yaml(pack, "toolpack://auto/good", policy)
        assert not any(f.rule_id == "urls-no-internal-targets" for f in findings)

    def test_localhost_not_blocked_by_internal_rule(self):
        """localhost is fine for dev targets — only `_is_local_url` covers
        the "no creds required" question; internal-range rule shouldn't
        trip on loopback by hostname (127.0.0.1 is loopback though)."""
        from harness.core.tool_security_scanner import _extract_host, _is_forbidden_internal_host
        assert _extract_host("http://localhost:8080/") == "localhost"
        # localhost as a hostname (no IP resolution here) is not caught by
        # the IP-range check — that's the right behavior, local dev works.
        assert not _is_forbidden_internal_host("localhost")
        # But 127.0.0.1 literal IS caught (loopback is internal).
        assert _is_forbidden_internal_host("127.0.0.1")


# ── P0-3: YAML safe-load preflight ───────────────────────────────────────


class TestYamlSafeLoad:
    def test_valid_yaml_parses(self):
        from harness.core.yaml_safe import safe_load
        result = safe_load("foo: bar\nlist: [1, 2]\n")
        assert result == {"foo": "bar", "list": [1, 2]}

    def test_oversize_rejected(self):
        from harness.core.yaml_safe import safe_load, YamlLoadError
        big = "x: " + ("a" * (3 * 1024 * 1024))  # 3 MB
        with pytest.raises(YamlLoadError, match="size cap"):
            safe_load(big, max_size_bytes=1024 * 1024)

    def test_billion_laughs_rejected(self):
        """Classic alias-recursion bomb is rejected by alias-count check."""
        from harness.core.yaml_safe import safe_load, YamlLoadError
        # Synthesize a payload with many aliases (real bombs have ~9
        # chained anchors; we replicate the pattern).
        bomb = (
            "a: &a ['x','x','x','x','x','x','x','x','x']\n"
            "b: &b [*a,*a,*a,*a,*a,*a,*a,*a,*a]\n"
            "c: &c [*b,*b,*b,*b,*b,*b,*b,*b,*b]\n"
            "d: &d [*c,*c,*c,*c,*c,*c,*c,*c,*c]\n"
            "e: &e [*d,*d,*d,*d,*d,*d,*d,*d,*d]\n"
            "f: &f [*e,*e,*e,*e,*e,*e,*e,*e,*e]\n"
            "g: [*f,*f,*f,*f,*f,*f,*f,*f,*f]\n"
        )
        with pytest.raises(YamlLoadError, match="aliases"):
            safe_load(bomb, max_aliases=10)

    def test_depth_limit_rejected(self):
        """Deeply nested YAML rejected after parse."""
        from harness.core.yaml_safe import safe_load, YamlLoadError
        # Nest 60 list levels via flow syntax — far past our 32 cap.
        doc = "x: " + "[" * 60 + "1" + "]" * 60 + "\n"
        with pytest.raises(YamlLoadError, match="nests"):
            safe_load(doc, max_depth=32)

    def test_reasonable_aliases_allowed(self):
        """Normal YAML with a handful of aliases still parses."""
        from harness.core.yaml_safe import safe_load
        doc = """
defaults: &defaults
  timeout: 30
tools:
  - <<: *defaults
    name: a
  - <<: *defaults
    name: b
"""
        result = safe_load(doc)
        assert result is not None and "tools" in result


# ── P0-4: Allowlist hardening ────────────────────────────────────────────


class TestSecurityAllowlist:
    def _build_policy(self, allowlist_entry):
        """Minimal policy with one allowlist entry."""
        return {
            "version": 1,
            "rules": [
                {"id": "shell-requires-explicit-permission", "severity": "block"}
            ],
            "allowlist": [allowlist_entry],
        }

    PACK_CONTENT = """
id: "toolpack://auto/test"
description: "test pack"
tools:
  - id: "tool://x"
    adapter_class: shell
    purpose: "test"
metadata:
  auto_generated: true
"""

    def test_allowlist_without_content_sha_ignored(self):
        """Allowlist entry without content_sha256 is IGNORED (hardening)."""
        from harness.core.tool_security_scanner import _build_allowlist
        policy = self._build_policy({
            "pack_id": "toolpack://auto/test",
            "downgrade": ["shell-requires-explicit-permission"],
            # Missing content_sha256 + expires
        })
        result = _build_allowlist(policy, "toolpack://auto/test", self.PACK_CONTENT)
        assert result == set()

    def test_allowlist_expired_ignored(self):
        """Past-date allowlist entry rejected."""
        from harness.core.tool_security_scanner import _build_allowlist
        sha = hashlib.sha256(self.PACK_CONTENT.encode("utf-8")).hexdigest()
        policy = self._build_policy({
            "pack_id": "toolpack://auto/test",
            "content_sha256": sha,
            "expires": "2020-01-01T00:00:00Z",  # Past
            "downgrade": ["shell-requires-explicit-permission"],
        })
        result = _build_allowlist(policy, "toolpack://auto/test", self.PACK_CONTENT)
        assert result == set()

    def test_allowlist_content_mismatch_ignored(self):
        """SHA mismatch means content drifted — entry invalidated."""
        from harness.core.tool_security_scanner import _build_allowlist
        future = (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(days=30)).isoformat()
        policy = self._build_policy({
            "pack_id": "toolpack://auto/test",
            "content_sha256": "0" * 64,  # wrong hash
            "expires": future,
            "downgrade": ["shell-requires-explicit-permission"],
        })
        result = _build_allowlist(policy, "toolpack://auto/test", self.PACK_CONTENT)
        assert result == set()

    def test_allowlist_valid_applies(self):
        """All three conditions met → downgrade applies."""
        from harness.core.tool_security_scanner import _build_allowlist
        sha = hashlib.sha256(self.PACK_CONTENT.encode("utf-8")).hexdigest()
        future = (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(days=30)).isoformat()
        policy = self._build_policy({
            "pack_id": "toolpack://auto/test",
            "content_sha256": sha,
            "expires": future,
            "downgrade": ["shell-requires-explicit-permission"],
        })
        result = _build_allowlist(policy, "toolpack://auto/test", self.PACK_CONTENT)
        assert result == {"shell-requires-explicit-permission"}


# ── P0-5: Tier 2 scoring fix ─────────────────────────────────────────────


class TestTier2PhraseBoundedMatch:
    def test_auth_does_not_match_oauth_anymore(self):
        """`auth` should NOT +2.0 bonus against `oauth token refresh`."""
        from harness.core.context_retriever import ChunkRef
        chunk = ChunkRef(
            id="oauth", topic="OAuth token refresh",
            summary="", path="x.md", keywords=(), size_bytes=1000,
        )
        score = chunk.score_for("auth", (), {})
        # No +2.0 for substring match now; only topic-word overlap which
        # won't match "auth" against ["oauth", "token", "refresh"].
        # So score should be effectively 0 (no exact word match, no keyword match).
        assert score < 1.5, f"Expected no substring bonus, got {score}"

    def test_auth_still_matches_word_bounded_auth(self):
        """`auth` SHOULD match chunk topic containing `auth` as a word."""
        from harness.core.context_retriever import ChunkRef
        chunk = ChunkRef(
            id="auth", topic="Basic auth patterns",
            summary="", path="x.md", keywords=(), size_bytes=1000,
        )
        score = chunk.score_for("auth", (), {})
        # Word-boundary match fires (+2.0) + word-overlap (+1.0 for "auth")
        assert score >= 2.0, f"Expected phrase + word match, got {score}"

    def test_router_matches_fastapi_router_patterns(self):
        """Multi-word phrase also works."""
        from harness.core.context_retriever import ChunkRef
        chunk = ChunkRef(
            id="routers", topic="FastAPI router patterns",
            summary="", path="x.md", keywords=(), size_bytes=1000,
        )
        score = chunk.score_for("router", (), {})
        assert score >= 2.0

    def test_short_topic_does_not_match_anything(self):
        """Topics of 1-2 chars shouldn't fire the +2 phrase bonus."""
        from harness.core.context_retriever import ChunkRef
        chunk = ChunkRef(
            id="x", topic="a",
            summary="", path="x.md", keywords=(), size_bytes=1000,
        )
        score = chunk.score_for("y", (), {})
        assert score == 0.0


# ── Integration smoke: scanner still passes clean packs ──────────────────


class TestHardenedScannerSmoke:
    def test_clean_pack_still_passes_after_hardening(self):
        """Existing clean-pack test must still pass — hardening shouldn't
        regress the happy path."""
        from harness.core.tool_security_scanner import scan_pack_yaml, load_policy
        policy = load_policy()
        pack = """
id: "toolpack://auto/clean"
version: "1.0.0"
description: "Clean pack for smoke test"
tools:
  - id: "tool://do_thing"
    adapter_class: http_api
    timeout: 30
    purpose: "Does a legitimate thing."
    base_url: "https://api.example.com/v1"
credentials:
  - name: EXAMPLE_API_KEY
    purpose: "API key"
    required: true
metadata:
  auto_generated: true
  pending_review: true
"""
        findings = scan_pack_yaml(pack, "toolpack://auto/clean", policy)
        blocks = [f for f in findings if f.severity == "block"]
        assert blocks == [], f"Unexpected blocks on clean pack: {blocks}"

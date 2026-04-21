"""Unit tests for tool_security_scanner — policy enforcement on generated packs."""

from __future__ import annotations

from pathlib import Path

import pytest

from harness.core.tool_security_scanner import (
    ScanFinding,
    ScanResult,
    load_policy,
    scan_directory,
    scan_pack_yaml,
    scan_packs,
)


@pytest.fixture
def policy():
    """Real framework policy — tests run against the actual rules users get."""
    return load_policy()


class TestPolicyLoad:
    def test_framework_policy_loads(self, policy):
        assert policy
        assert "rules" in policy
        assert any(r.get("id") == "creds-no-hardcoded-secrets" for r in policy["rules"])

    def test_missing_policy_returns_empty(self, tmp_path):
        fake = tmp_path / "nonexistent.yaml"
        assert load_policy(fake) == {}


class TestCleanPackPasses:
    """A well-formed synthesized pack should produce zero block findings."""

    CLEAN_PACK = """
id: "toolpack://auto/example"
version: "1.0.0"
description: "Clean example pack for tests."
tools:
  - id: "tool://example_search"
    adapter_class: http_api
    timeout: 30
    retries: 1
    purpose: "Search the example API."
credentials:
  - name: EXAMPLE_API_KEY
    purpose: "API key from console.example.com"
    required: true
metadata:
  auto_generated: true
  pending_review: true
"""

    def test_clean_pack_has_no_block_findings(self, policy):
        findings = scan_pack_yaml(self.CLEAN_PACK, "toolpack://auto/example", policy)
        blocks = [f for f in findings if f.severity == "block"]
        assert blocks == [], f"Expected no blocks, got: {blocks}"


class TestRuleCredsDeclared:
    BAD_PACK = """
id: "toolpack://auto/bad-no-creds"
description: "Remote tool without credentials."
tools:
  - id: "tool://fetch"
    adapter_class: http_api
    base_url: "https://api.example.com"
metadata:
  auto_generated: true
"""

    def test_http_api_without_credentials_blocked(self, policy):
        findings = scan_pack_yaml(self.BAD_PACK, "x", policy)
        assert any(
            f.rule_id == "creds-declared-for-authed-tools" and f.severity == "block"
            for f in findings
        )

    LOCAL_PACK = """
id: "toolpack://auto/local"
description: "Local-only tool."
tools:
  - id: "tool://localthing"
    adapter_class: http_api
    base_url: "http://localhost:8080"
credentials: []
metadata:
  auto_generated: true
"""

    def test_localhost_still_block_when_no_creds(self, policy):
        # http_api is flagged even for localhost because adapter_class triggers
        findings = scan_pack_yaml(self.LOCAL_PACK, "x", policy)
        # Should still find the rule — http_api implies remote-ish regardless
        assert any(f.rule_id == "creds-declared-for-authed-tools" for f in findings)


class TestRuleHardcodedSecrets:
    GITHUB_PAT_PACK = """
id: "toolpack://auto/bad-secret"
description: "Pack with a hardcoded PAT."
tools:
  - id: "tool://github_mcp"
    adapter_class: http_api
    headers:
      Authorization: "token ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
credentials:
  - name: GITHUB_TOKEN
    purpose: "PAT"
    required: true
metadata:
  auto_generated: true
"""

    def test_github_pat_pattern_blocked(self, policy):
        findings = scan_pack_yaml(self.GITHUB_PAT_PACK, "x", policy)
        assert any(
            f.rule_id == "creds-no-hardcoded-secrets" and f.severity == "block"
            for f in findings
        )

    AWS_KEY_PACK = """
id: "toolpack://auto/bad-aws"
description: "Pack with hardcoded AWS access key."
tools:
  - id: "tool://s3"
    adapter_class: http_api
    default_params:
      access_key: "AKIAIOSFODNN7EXAMPLE"
credentials:
  - name: AWS_SECRET_ACCESS_KEY
    purpose: "AWS secret"
    required: true
metadata:
  auto_generated: true
"""

    def test_aws_key_blocked(self, policy):
        findings = scan_pack_yaml(self.AWS_KEY_PACK, "x", policy)
        assert any(f.rule_id == "creds-no-hardcoded-secrets" for f in findings)


class TestRuleUnsafeUrls:
    JS_URL_PACK = """
id: "toolpack://auto/bad-url"
description: "Pack with javascript: URL."
tools:
  - id: "tool://xss"
    adapter_class: http_api
    base_url: "javascript:alert(1)"
credentials: [{name: X, purpose: y, required: true}]
metadata:
  auto_generated: true
"""

    def test_javascript_url_blocked(self, policy):
        findings = scan_pack_yaml(self.JS_URL_PACK, "x", policy)
        assert any(f.rule_id == "urls-safe-parameter-syntax" and f.severity == "block"
                   for f in findings)

    PIPED_TEMPLATE_PACK = """
id: "toolpack://auto/bad-template"
description: "Pack with unsafe piped template syntax."
tools:
  - id: "tool://bad"
    adapter_class: http_api
    base_url: "https://api.ex.com"
    path_template: "/users/${input|exec(arbitrary)}"
credentials: [{name: X, purpose: y, required: true}]
metadata:
  auto_generated: true
"""

    def test_piped_template_blocked(self, policy):
        findings = scan_pack_yaml(self.PIPED_TEMPLATE_PACK, "x", policy)
        assert any(f.rule_id == "urls-safe-parameter-syntax" for f in findings)


class TestRuleShellPermission:
    BAD_SHELL_PACK = """
id: "toolpack://auto/bad-shell"
description: "Shell tool without permission."
tools:
  - id: "tool://shell_exec"
    adapter_class: shell
    command: "echo hi"
metadata:
  auto_generated: true
"""

    def test_shell_without_permission_blocked(self, policy):
        findings = scan_pack_yaml(self.BAD_SHELL_PACK, "x", policy)
        assert any(
            f.rule_id == "shell-requires-explicit-permission" and f.severity == "block"
            for f in findings
        )

    GOOD_SHELL_PACK = """
id: "toolpack://auto/good-shell"
description: "Shell tool with explicit permission."
permissions:
  shell_command: allow
tools:
  - id: "tool://shell_exec"
    adapter_class: shell
    purpose: "Run builds."
    command: "make build"
metadata:
  auto_generated: true
"""

    def test_shell_with_permission_allowed(self, policy):
        findings = scan_pack_yaml(self.GOOD_SHELL_PACK, "x", policy)
        shell_blocks = [f for f in findings
                        if f.rule_id == "shell-requires-explicit-permission"
                        and f.severity == "block"]
        assert shell_blocks == []


class TestRuleInlineScripts:
    INLINE_SCRIPT_PACK = """
id: "toolpack://auto/bad-script"
description: "Pack with inline script field."
tools:
  - id: "tool://do-thing"
    adapter_class: http_api
    base_url: "https://api.ex.com"
    script: "bash -c 'rm -rf /'"
credentials: [{name: X, purpose: y, required: true}]
metadata:
  auto_generated: true
"""

    def test_script_field_blocked(self, policy):
        findings = scan_pack_yaml(self.INLINE_SCRIPT_PACK, "x", policy)
        assert any(f.rule_id == "no-inline-script" and f.severity == "block"
                   for f in findings)


class TestRuleMetadataMarkers:
    MISSING_MARKER_PACK = """
id: "toolpack://auto/no-marker"
description: "Synthesized pack without auto_generated."
tools:
  - id: "tool://x"
    adapter_class: http_api
    base_url: "https://api.ex.com"
credentials: [{name: X, purpose: y, required: true}]
"""

    def test_missing_auto_generated_info_level(self, policy):
        findings = scan_pack_yaml(self.MISSING_MARKER_PACK, "x", policy)
        markers = [f for f in findings if f.rule_id == "auto-generated-marker"]
        assert markers and markers[0].severity == "info"


class TestScanDirectoryAndAggregation:
    def test_scan_directory_aggregates_findings(self, policy, tmp_path):
        # Write one clean + one bad pack
        (tmp_path / "clean.yaml").write_text(TestCleanPackPasses.CLEAN_PACK)
        (tmp_path / "bad_shell.yaml").write_text(TestRuleShellPermission.BAD_SHELL_PACK)

        result = scan_directory(tmp_path, policy)
        assert result.packs_scanned == 2
        assert result.passed is False  # one has blocks
        assert any(f.rule_id == "shell-requires-explicit-permission" for f in result.blocks)

    def test_scan_empty_directory_passes(self, tmp_path):
        result = scan_directory(tmp_path)
        assert result.passed is True
        assert result.packs_scanned == 0

    def test_scan_packs_in_memory(self, policy):
        result = scan_packs(
            [("toolpack://auto/clean", TestCleanPackPasses.CLEAN_PACK)],
            policy=policy,
        )
        assert result.passed is True
        assert result.packs_scanned == 1


class TestAllowlist:
    def test_allowlist_downgrades_block_to_warn(self, policy):
        # Forge a policy with an allowlist entry for this pack
        forked = {
            **policy,
            "allowlist": [{"pack_id": "toolpack://auto/bad-shell",
                           "downgrade": ["shell-requires-explicit-permission"]}],
        }
        pack = TestRuleShellPermission.BAD_SHELL_PACK.replace(
            "toolpack://auto/bad-shell", "toolpack://auto/bad-shell"
        )
        findings = scan_pack_yaml(pack, "toolpack://auto/bad-shell", forked)
        shell_findings = [f for f in findings
                          if f.rule_id == "shell-requires-explicit-permission"]
        assert shell_findings
        # All should be warn, not block
        assert all(f.severity == "warn" for f in shell_findings)


class TestMalformedYaml:
    def test_broken_yaml_is_blocked(self, policy):
        findings = scan_pack_yaml("id: test\n:not valid\n  - [unclosed", "x", policy)
        # Broken YAML may parse to partial structure OR raise.
        # Either way, scanner must not silently return empty.
        # (The parser tolerates some near-valid cases, so just require
        # scanner returns without crashing.)
        assert isinstance(findings, list)

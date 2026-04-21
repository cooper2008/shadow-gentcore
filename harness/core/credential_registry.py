"""CredentialRegistry — tool-pack declarations × runtime resolution.

Three jobs:

  1. Index every known tool's credential requirements, sourced from
     `agent-tools` tool-pack YAMLs (uniform `credentials:` field per tool,
     with back-compat for the legacy `credential_source: "env:X"` alias).

  2. Compute the *derived* set of credentials an agent needs by unioning
     across its declared tools. This is called at agent-load time — no
     hand-authored list on the agent manifest is required or trusted.

  3. Resolve a credential value through a pluggable backend (env / file /
     AWS / Vault / chained), and validate that every required credential
     resolves to a non-empty string. Missing values raise
     MissingCredentialError with a clear, actionable message.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable

from harness.core.credential_backends import (
    CredentialBackend,
    EnvBackend,
)


logger = logging.getLogger(__name__)


# ── Types ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CredentialRequirement:
    """One credential a tool declares it needs.

    `auth_kind` is a coarse classifier used by the Builder's
    REQUIRED_CREDENTIALS.md generator to group related creds (e.g. the
    `client_id` / `client_secret` / `refresh_token` trio of an OAuth2
    app) so the team-lead checklist shows one OAuth setup block instead
    of three opaque env-var rows. Values:
      - `api_key`   (default): simple token / email / URL
      - `oauth2`    : part of an OAuth 2.0 client + refresh_token setup
      - `basic`     : username + password pair
      - `mtls`      : mutual TLS cert + key
      - `aws_iam`   : AWS access key / secret / optional session token
    """

    name: str                          # canonical name (e.g. "JIRA_API_TOKEN")
    purpose: str = ""                  # human-readable explainer + "how to get it" hint
    required: bool = True              # False = optional; absence is not an error
    declared_by: tuple[str, ...] = ()  # tool names that require this cred
    auth_kind: str = "api_key"         # coarse grouping for UI / docs
    oauth_group: str = ""              # identifier grouping multiple OAuth creds
                                       # for the same app (e.g. "google-drive")
    oauth_scopes: tuple[str, ...] = () # scopes requested (oauth2 only)

    def with_declarer(self, tool_name: str) -> CredentialRequirement:
        """Return a copy with `tool_name` appended to declared_by."""
        if tool_name in self.declared_by:
            return self
        return CredentialRequirement(
            name=self.name,
            purpose=self.purpose,
            required=self.required,
            declared_by=(*self.declared_by, tool_name),
            auth_kind=self.auth_kind,
            oauth_group=self.oauth_group,
            oauth_scopes=self.oauth_scopes,
        )


@dataclass
class ValidationReport:
    """Outcome of validate(). Lists resolved + missing requirements."""

    resolved: list[CredentialRequirement] = field(default_factory=list)
    missing: list[CredentialRequirement] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.missing

    def format_cli(self) -> str:
        lines: list[str] = []
        if self.resolved:
            lines.append(f"✓ {len(self.resolved)} credential(s) resolved:")
            for req in self.resolved:
                declarers = ", ".join(req.declared_by) if req.declared_by else "—"
                lines.append(f"    {req.name:30s} used by: {declarers}")
        if self.missing:
            lines.append(f"✗ {len(self.missing)} credential(s) MISSING:")
            for req in self.missing:
                declarers = ", ".join(req.declared_by) if req.declared_by else "—"
                lines.append(f"    {req.name:30s} needed by: {declarers}")
                if req.purpose:
                    lines.append(f"      purpose: {req.purpose}")
        return "\n".join(lines) if lines else "(no credentials required)"


class MissingCredentialError(RuntimeError):
    """Raised when validate() finds a required credential isn't resolved."""

    def __init__(self, report: ValidationReport, agent_id: str | None = None) -> None:
        self.report = report
        self.agent_id = agent_id
        missing_names = ", ".join(r.name for r in report.missing)
        who = f"agent '{agent_id}'" if agent_id else "agents"
        lines = [
            f"{who} require(s) credentials that are not resolved: {missing_names}",
            "",
        ]
        for req in report.missing:
            declarers = ", ".join(req.declared_by) if req.declared_by else "—"
            lines.append(f"  - {req.name}")
            if req.purpose:
                lines.append(f"    purpose:  {req.purpose}")
            lines.append(f"    used by:  {declarers}")
        lines.append("")
        lines.append(
            "Configure via: `export <NAME>=<value>` OR `~/.gentcore/credentials.json` "
            "OR `config/credentials.yaml`. See docs/CREDENTIALS_GUIDE.md."
        )
        super().__init__("\n".join(lines))


# ── Registry ──────────────────────────────────────────────────────────────


class CredentialRegistry:
    """Global index: tool_name → credential requirements, plus resolution."""

    def __init__(self, backend: CredentialBackend | None = None) -> None:
        self._backend: CredentialBackend = backend or EnvBackend()
        self._tool_requirements: dict[str, list[CredentialRequirement]] = {}

    # ── Registration ─────────────────────────────────────────────────────

    def register_tool(
        self,
        tool_name: str,
        requirements: Iterable[CredentialRequirement | dict[str, Any]],
    ) -> None:
        """Record the credentials a given tool needs.

        Accepts either CredentialRequirement instances or plain dicts (from
        YAML). Idempotent — re-registering overwrites.
        """
        parsed: list[CredentialRequirement] = []
        for req in requirements:
            if isinstance(req, CredentialRequirement):
                parsed.append(req.with_declarer(tool_name))
                continue
            if not isinstance(req, dict):
                continue
            name = str(req.get("name", "")).strip()
            if not name:
                continue
            scopes_raw = req.get("oauth_scopes") or req.get("scopes") or ()
            if isinstance(scopes_raw, str):
                scopes_raw = [s.strip() for s in scopes_raw.split(",") if s.strip()]
            parsed.append(CredentialRequirement(
                name=name,
                purpose=str(req.get("purpose", "")),
                required=bool(req.get("required", True)),
                declared_by=(tool_name,),
                auth_kind=str(req.get("auth_kind", "api_key")),
                oauth_group=str(req.get("oauth_group", "")),
                oauth_scopes=tuple(str(s) for s in scopes_raw),
            ))
        if parsed:
            self._tool_requirements[tool_name] = parsed

    def register_tool_pack(self, pack_manifest: dict[str, Any]) -> int:
        """Ingest an entire tool-pack YAML. Returns number of tools registered.

        Per-tool credential declarations take highest priority; otherwise a
        tool inherits its pack's defaults so the common case (every tool in
        `toolpack://services/jira` needs `JIRA_*`) only needs to be written
        once.

        Resolution per tool, in order:
          1. tool-level `credentials: [...]`         (new shape, per-tool)
          2. tool-level `credential_source: "env:X"` (legacy alias, per-tool)
          3. pack-level `credentials: [...]`         (new shape, pack-wide)
          4. pack-level `default_policy.credentials: [...]`  (alt pack-wide)
          5. pack-level `default_policy.credential_source: "env:X"`  (legacy)

        Tool `id` values may be either the short name (`jira_search`) or the
        `tool://jira_search` URI — both are normalised to the short name for
        indexing so agents declaring either form find their requirements.
        """
        pack_defaults = self._extract_pack_defaults(pack_manifest)
        count = 0
        for tool in pack_manifest.get("tools", []) or []:
            if not isinstance(tool, dict):
                continue
            raw_name = tool.get("name") or tool.get("id") or ""
            tool_name = str(raw_name).removeprefix("tool://")
            if not tool_name:
                continue
            reqs = self._normalise_credentials(
                tool.get("credentials") or tool.get("credential_source"),
            )
            if not reqs:
                reqs = pack_defaults
            if reqs:
                self.register_tool(tool_name, reqs)
                count += 1
        return count

    @staticmethod
    def _extract_pack_defaults(pack_manifest: dict[str, Any]) -> list[dict[str, Any]]:
        """Read pack-level default credential declarations."""
        reg = CredentialRegistry._normalise_credentials(pack_manifest.get("credentials"))
        if reg:
            return reg
        policy = pack_manifest.get("default_policy") or {}
        if isinstance(policy, dict):
            reg = CredentialRegistry._normalise_credentials(
                policy.get("credentials") or policy.get("credential_source"),
            )
            if reg:
                return reg
        return []

    @staticmethod
    def _normalise_credentials(value: Any) -> list[dict[str, Any]]:
        """Accept a list of dicts (new shape) OR a single legacy 'env:X' scalar."""
        if not value:
            return []
        if isinstance(value, list):
            out: list[dict[str, Any]] = []
            for entry in value:
                if isinstance(entry, dict) and entry.get("name"):
                    out.append(entry)
                elif isinstance(entry, str) and entry.startswith("env:"):
                    out.append({"name": entry.split(":", 1)[1], "required": True})
            return out
        if isinstance(value, str) and value.startswith("env:"):
            return [{"name": value.split(":", 1)[1], "required": True}]
        return []

    # ── Query ────────────────────────────────────────────────────────────

    def required_for_tool(self, tool_name: str) -> list[CredentialRequirement]:
        """The list this tool declared. Empty if the tool is unknown."""
        return list(self._tool_requirements.get(tool_name, []))

    def required_for_tools(
        self, tool_names: Iterable[str],
    ) -> list[CredentialRequirement]:
        """Union across multiple tools. Each cred appears once, aggregating declarers."""
        by_name: dict[str, CredentialRequirement] = {}
        for tool_name in tool_names:
            for req in self._tool_requirements.get(tool_name, []):
                existing = by_name.get(req.name)
                if existing is None:
                    by_name[req.name] = req
                else:
                    merged_decl = tuple(dict.fromkeys((*existing.declared_by, *req.declared_by)))
                    by_name[req.name] = CredentialRequirement(
                        name=req.name,
                        # Keep the first non-empty purpose we saw.
                        purpose=existing.purpose or req.purpose,
                        required=existing.required or req.required,
                        declared_by=merged_decl,
                    )
        return list(by_name.values())

    def required_for_agent(
        self, agent_manifest: dict[str, Any],
    ) -> list[CredentialRequirement]:
        """Derive the union set from an agent manifest's declared tools."""
        tool_names: list[str] = []
        for tool in agent_manifest.get("tools", []) or []:
            if isinstance(tool, dict):
                name = tool.get("name")
            else:
                name = tool
            if name:
                tool_names.append(str(name))
        return self.required_for_tools(tool_names)

    # ── Resolution + validation ──────────────────────────────────────────

    def resolve(self, cred_name: str) -> str | None:
        """Ask the backend to look up a credential by name."""
        return self._backend.resolve(cred_name)

    def validate(
        self,
        tool_names: Iterable[str],
        *,
        agent_id: str | None = None,
        raise_on_missing: bool = False,
    ) -> ValidationReport:
        """Check that every required credential for `tool_names` resolves.

        Optional credentials that don't resolve land in `resolved` with a
        None value conceptually (but we don't track values on the report;
        missing only contains *required* unresolved credentials).
        """
        required = self.required_for_tools(tool_names)
        report = ValidationReport()
        for req in required:
            value = self._backend.resolve(req.name)
            if value:
                report.resolved.append(req)
            elif req.required:
                report.missing.append(req)
        if raise_on_missing and report.missing:
            raise MissingCredentialError(report, agent_id=agent_id)
        return report

    # ── Introspection ────────────────────────────────────────────────────

    @property
    def known_tools(self) -> list[str]:
        return list(self._tool_requirements.keys())

    @property
    def backend(self) -> CredentialBackend:
        return self._backend

    def set_backend(self, backend: CredentialBackend) -> None:
        self._backend = backend

"""Built-in tool adapters — bridge between tool names and shell commands.

ToolExecutor calls: adapter.invoke(tool_name: str, arguments: dict)
These adapters translate to concrete subprocess calls.
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import urllib.parse
from pathlib import Path
from typing import Any

import httpx


def _q(s: str) -> str:
    """Shell-quote a string safely using shlex."""
    return shlex.quote(str(s))


def _uq(s: str) -> str:
    """URL-encode a string for safe use in query parameters."""
    return urllib.parse.quote(str(s), safe="")


class BuiltinToolAdapter:
    """Executes a built-in tool by running a shell command derived from arguments."""

    def __init__(self, command_builder: Any) -> None:
        self._command_builder = command_builder

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            cmd = self._command_builder(arguments)
        except KeyError as exc:
            return {"success": False, "stdout": "", "stderr": f"Missing argument: {exc}", "exit_code": 1}

        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=120.0)
            return {
                "success": proc.returncode == 0,
                "stdout": stdout_bytes.decode(errors="replace"),
                "stderr": stderr_bytes.decode(errors="replace"),
                "exit_code": proc.returncode,
                "command": cmd,
            }
        except asyncio.TimeoutError:
            return {"success": False, "stdout": "", "stderr": "Timed out", "exit_code": -1, "command": cmd}
        except Exception as exc:
            return {"success": False, "stdout": "", "stderr": str(exc), "exit_code": -1, "command": cmd}


class FileWriteAdapter:
    """Writes content to a file (not a shell command — pure Python for safety)."""

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        path = arguments.get("path", "")
        content = arguments.get("content", "")
        if not path:
            return {"success": False, "stderr": "Missing 'path' argument", "exit_code": 1}
        try:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return {
                "success": True,
                "stdout": f"Wrote {len(content)} bytes to {path}",
                "stderr": "",
                "exit_code": 0,
            }
        except Exception as exc:
            return {"success": False, "stdout": "", "stderr": str(exc), "exit_code": 1}


class HTTPServiceAdapter:
    """Invokes an HTTP service endpoint using httpx — no shell subprocess.

    Eliminates curl-based shell injection for all HTTP service tools.
    Arguments are passed directly as request body/query params, never
    interpolated into shell strings.
    """

    def __init__(self, build_request_fn: Any) -> None:
        self._build = build_request_fn

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            req = self._build(arguments)
        except KeyError as exc:
            return {"success": False, "stdout": "", "stderr": f"Missing argument: {exc}", "exit_code": 1}
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.request(
                    method=req.get("method", "GET"),
                    url=req["url"],
                    headers=req.get("headers", {}),
                    json=req.get("json"),
                    params=req.get("params"),
                )
                body = resp.text
                return {
                    "success": resp.is_success,
                    "status_code": resp.status_code,
                    "stdout": body,
                    "stderr": "",
                    "exit_code": 0 if resp.is_success else 1,
                }
        except Exception as exc:
            return {"success": False, "stdout": "", "stderr": str(exc), "exit_code": 1}


# ---------------------------------------------------------------------------
# Built-in tool registry: tool_name → BuiltinToolAdapter
# ---------------------------------------------------------------------------

def _make(fn: Any) -> BuiltinToolAdapter:
    return BuiltinToolAdapter(fn)


def _http(fn: Any) -> HTTPServiceAdapter:
    return HTTPServiceAdapter(fn)


def _jira_headers() -> dict[str, str]:
    import base64
    creds = f"{os.environ.get('JIRA_EMAIL', '')}:{os.environ.get('JIRA_API_TOKEN', '')}"
    auth = base64.b64encode(creds.encode()).decode()
    return {"Authorization": f"Basic {auth}", "Content-Type": "application/json"}


def _confluence_headers() -> dict[str, str]:
    import base64
    creds = f"{os.environ.get('CONFLUENCE_EMAIL', '')}:{os.environ.get('CONFLUENCE_API_TOKEN', '')}"
    auth = base64.b64encode(creds.encode()).decode()
    return {"Authorization": f"Basic {auth}", "Content-Type": "application/json"}


def _slack_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {os.environ.get('SLACK_BOT_TOKEN', '')}", "Content-Type": "application/json"}


def _pd_headers() -> dict[str, str]:
    return {"Authorization": f"Token token={os.environ.get('PAGERDUTY_API_TOKEN', '')}", "Content-Type": "application/json"}


def _dd_headers() -> dict[str, str]:
    return {"DD-API-KEY": os.environ.get("DD_API_KEY", ""), "DD-APPLICATION-KEY": os.environ.get("DD_APP_KEY", "")}


def _linear_headers() -> dict[str, str]:
    return {"Authorization": os.environ.get("LINEAR_API_KEY", ""), "Content-Type": "application/json"}


def _notion_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {os.environ.get('NOTION_API_TOKEN', '')}", "Notion-Version": "2022-06-28", "Content-Type": "application/json"}


def _shortcut_headers() -> dict[str, str]:
    return {"Shortcut-Token": os.environ.get("SHORTCUT_API_TOKEN", ""), "Content-Type": "application/json"}


def _sonar_headers() -> dict[str, str]:
    import base64
    creds = f"{os.environ.get('SONAR_TOKEN', '')}:"
    auth = base64.b64encode(creds.encode()).decode()
    return {"Authorization": f"Basic {auth}"}


def _codecov_headers() -> dict[str, str]:
    return {"Authorization": f"token {os.environ.get('CODECOV_TOKEN', '')}"}


# ---------------------------------------------------------------------------
# Service tool command builders — defined as named functions to avoid
# backslash-in-f-string-expression syntax errors on Python < 3.12.
# ---------------------------------------------------------------------------

def _gh_pr_review(a: dict[str, Any]) -> str:
    number = _q(str(a.get("number", "")))
    if a.get("approve"):
        return f"gh pr review {number} --approve 2>&1"
    return f"gh pr view {number} 2>&1"


BUILTIN_ADAPTERS: dict[str, Any] = {
    # Filesystem
    "file_read": _make(lambda a: f"cat {_q(a['path'])}"),
    "file_write": FileWriteAdapter(),
    "file_list": _make(lambda a: f"ls -la {_q(a.get('path', '.'))}"),
    "file_delete": _make(lambda a: f"rm -f {_q(a['path'])}"),
    "file_exists": _make(lambda a: f"test -f {_q(a['path'])} && echo exists || echo not_found"),

    # Shell — intentionally passes raw command (agent-controlled, gated by RuleEngine)
    "shell_exec": _make(lambda a: a.get("command", a.get("cmd", "echo 'no command given'"))),

    # Search
    "search_code": _make(lambda a: f"grep -rn {_q(a.get('pattern', ''))} {_q(a.get('path', '.'))} 2>/dev/null || true"),
    "search_files": _make(lambda a: f"find {_q(a.get('path', '.'))} -name {_q(a.get('pattern', '*'))} 2>/dev/null"),
    "list_dir": _make(lambda a: f"ls -la {_q(a.get('path', '.'))}"),

    # FastAPI
    "pytest_asyncio": _make(lambda a: f"pytest {_q(a.get('path', 'tests/'))} -v --asyncio-mode=auto 2>&1"),
    "ruff_check": _make(lambda a: f"ruff check {_q(a.get('path', '.'))} 2>&1"),
    "uvicorn_run": _make(lambda a: f"timeout 5 uvicorn {_q(a.get('app', 'main:app'))} --host 0.0.0.0 --port 8000 2>&1 || true"),
    "alembic_migrate": _make(lambda a: f"alembic upgrade {_q(a.get('revision', 'head'))} 2>&1"),
    "pip_install": _make(lambda a: f"pip install {_q(a.get('package', '-r requirements.txt'))} 2>&1"),

    # React / TypeScript
    "npm_install": _make(lambda a: f"npm install --prefix {_q(a.get('path', '.'))} 2>&1"),
    "npm_run_build": _make(lambda a: f"npm run build --prefix {_q(a.get('path', '.'))} 2>&1"),
    "npm_run_test": _make(lambda a: f"npm test -- --watchAll=false --prefix {_q(a.get('path', '.'))} 2>&1"),
    "tsc_check": _make(lambda a: f"npx tsc --noEmit --project {_q(a.get('path', 'tsconfig.json'))} 2>&1"),
    "eslint_check": _make(lambda a: f"npx eslint {_q(a.get('path', 'src/'))} 2>&1"),

    # GitHub (gh CLI)
    "gh_create_pr": _make(lambda a: f"gh pr create --title {_q(a.get('title', ''))} --body {_q(a.get('body', ''))} 2>&1"),
    "gh_list_issues": _make(lambda a: f"gh issue list --repo {_q(a.get('repo', ''))} --limit {_q(str(a.get('limit', 20)))} 2>&1"),
    "gh_create_issue": _make(lambda a: f"gh issue create --title {_q(a.get('title', ''))} --body {_q(a.get('body', ''))} 2>&1"),
    "gh_comment": _make(lambda a: f"gh issue comment {_q(str(a.get('number', '')))} --body {_q(a.get('body', ''))} 2>&1"),
    "gh_pr_review": _make(_gh_pr_review),

    # Jira (httpx-based HTTP — no curl shell injection)
    "jira_create_issue": _http(lambda a: {
        "method": "POST", "url": f"{os.environ.get('JIRA_BASE_URL', '')}/rest/api/3/issue",
        "headers": _jira_headers(), "json": a.get("fields", {}),
    }),
    "jira_search": _http(lambda a: {
        "method": "GET", "url": f"{os.environ.get('JIRA_BASE_URL', '')}/rest/api/3/search",
        "headers": _jira_headers(), "params": {"jql": a.get("jql", "")},
    }),
    "jira_transition": _http(lambda a: {
        "method": "POST", "url": f"{os.environ.get('JIRA_BASE_URL', '')}/rest/api/3/issue/{a.get('issue_key', '')}/transitions",
        "headers": _jira_headers(), "json": {"transition": {"id": str(a.get("transition_id", ""))}},
    }),
    "jira_comment": _http(lambda a: {
        "method": "POST", "url": f"{os.environ.get('JIRA_BASE_URL', '')}/rest/api/3/issue/{a.get('issue_key', '')}/comment",
        "headers": _jira_headers(),
        "json": {"body": {"content": [{"content": [{"text": a.get("body", ""), "type": "text"}], "type": "paragraph"}], "type": "doc", "version": 1}},
    }),

    # Confluence (httpx-based HTTP)
    "confluence_create_page": _http(lambda a: {
        "method": "POST", "url": f"{os.environ.get('CONFLUENCE_BASE_URL', '')}/rest/api/content",
        "headers": _confluence_headers(),
        "json": {"type": "page", "title": a.get("title", ""), "space": {"key": a.get("space_key", "")},
                 "body": {"storage": {"value": a.get("content", ""), "representation": "storage"}}},
    }),
    "confluence_update_page": _http(lambda a: {
        "method": "PUT", "url": f"{os.environ.get('CONFLUENCE_BASE_URL', '')}/rest/api/content/{a.get('page_id', '')}",
        "headers": _confluence_headers(), "json": a.get("payload", {}),
    }),
    "confluence_search": _http(lambda a: {
        "method": "GET", "url": f"{os.environ.get('CONFLUENCE_BASE_URL', '')}/rest/api/content/search",
        "headers": _confluence_headers(), "params": {"cql": a.get("cql", "")},
    }),

    # Slack (httpx-based HTTP)
    "slack_send_message": _http(lambda a: {
        "method": "POST", "url": "https://slack.com/api/chat.postMessage",
        "headers": _slack_headers(), "json": {"channel": a.get("channel", ""), "text": a.get("text", "")},
    }),
    "slack_create_channel": _http(lambda a: {
        "method": "POST", "url": "https://slack.com/api/conversations.create",
        "headers": _slack_headers(), "json": {"name": a.get("name", "")},
    }),

    # ── Frontend tools ──
    "vite_dev": _make(lambda a: f"npx vite --port {_q(str(a.get('port', 3000)))} 2>&1 &"),
    "vite_build": _make(lambda a: f"npx vite build {_q(a.get('path', '.'))} 2>&1"),
    "vite_preview": _make(lambda a: f"npx vite preview --port {_q(str(a.get('port', 4173)))} 2>&1 &"),
    "storybook_build": _make(lambda a: f"npx storybook build -o {_q(a.get('output', 'storybook-static'))} 2>&1"),
    "storybook_test": _make(lambda a: "npx test-storybook 2>&1"),
    "playwright_test": _make(lambda a: f"npx playwright test {_q(a.get('path', ''))} 2>&1"),
    "playwright_codegen": _make(lambda a: f"npx playwright codegen {_q(a.get('url', ''))} 2>&1"),

    # ── Documentation tools ──
    "sphinx_build": _make(lambda a: f"sphinx-build -b html {_q(a.get('source', 'docs/'))} {_q(a.get('output', 'docs/_build'))} 2>&1"),
    "sphinx_apidoc": _make(lambda a: f"sphinx-apidoc -o {_q(a.get('output', 'docs/'))} {_q(a.get('source', 'src/'))} 2>&1"),
    "typedoc_generate": _make(lambda a: f"npx typedoc --out {_q(a.get('output', 'docs/'))} {_q(a.get('entry', 'src/index.ts'))} 2>&1"),
    "mkdocs_build": _make(lambda a: f"mkdocs build -d {_q(a.get('output', 'site/'))} 2>&1"),
    "mkdocs_serve": _make(lambda a: f"mkdocs serve --dev-addr {_q(a.get('addr', '127.0.0.1:8000'))} 2>&1 &"),
    "swagger_generate": _make(lambda a: f"npx @openapitools/openapi-generator-cli generate -i {_q(a.get('spec', 'openapi.yaml'))} -g {_q(a.get('lang', 'html2'))} -o {_q(a.get('output', 'docs/api'))} 2>&1"),
    "swagger_validate": _make(lambda a: f"npx @openapitools/openapi-generator-cli validate -i {_q(a.get('spec', 'openapi.yaml'))} 2>&1"),

    # ── Observability tools ──
    "pd_create_incident": _http(lambda a: {
        "method": "POST", "url": "https://api.pagerduty.com/incidents",
        "headers": _pd_headers(), "json": a.get("payload", {}),
    }),
    "pd_list_incidents": _http(lambda a: {
        "method": "GET", "url": "https://api.pagerduty.com/incidents",
        "headers": _pd_headers(), "params": {"statuses[]": a.get("status", "triggered")},
    }),
    "pd_acknowledge": _http(lambda a: {
        "method": "PUT", "url": f"https://api.pagerduty.com/incidents/{a.get('id', '')}",
        "headers": _pd_headers(), "json": {"incident": {"type": "incident_reference", "status": "acknowledged"}},
    }),
    "pd_resolve": _http(lambda a: {
        "method": "PUT", "url": f"https://api.pagerduty.com/incidents/{a.get('id', '')}",
        "headers": _pd_headers(), "json": {"incident": {"type": "incident_reference", "status": "resolved"}},
    }),
    "dd_query_metrics": _http(lambda a: {
        "method": "GET", "url": "https://api.datadoghq.com/api/v1/query",
        "headers": _dd_headers(), "params": {"query": a.get("query", ""), "from": a.get("from", ""), "to": a.get("to", "")},
    }),
    "dd_list_monitors": _http(lambda a: {
        "method": "GET", "url": "https://api.datadoghq.com/api/v1/monitor", "headers": _dd_headers(),
    }),
    "dd_get_events": _http(lambda a: {
        "method": "GET", "url": "https://api.datadoghq.com/api/v1/events",
        "headers": _dd_headers(), "params": {"start": a.get("start", ""), "end": a.get("end", "")},
    }),
    "cw_query_logs": _make(lambda a: f"aws logs filter-log-events --log-group-name {_q(a.get('log_group', ''))} --filter-pattern {_q(a.get('pattern', ''))} --limit {_q(str(a.get('limit', 50)))} 2>&1"),
    "cw_get_metrics": _make(lambda a: f"aws cloudwatch get-metric-statistics --namespace {_q(a.get('namespace', ''))} --metric-name {_q(a.get('metric', ''))} --period {_q(str(a.get('period', 300)))} --statistics Average --start-time {_q(a.get('start', ''))} --end-time {_q(a.get('end', ''))} 2>&1"),
    "cw_list_alarms": _make(lambda a: f"aws cloudwatch describe-alarms --state-value {_q(a.get('state', 'ALARM'))} 2>&1"),

    # ── PM tools ──
    "linear_create_issue": _http(lambda a: {
        "method": "POST", "url": "https://api.linear.app/graphql", "headers": _linear_headers(),
        "json": {"query": f'mutation {{ issueCreate(input: {{title: "{a.get("title", "")}", teamId: "{a.get("team_id", "")}"}}) {{ success issue {{ id url }} }} }}'},
    }),
    "linear_update_issue": _http(lambda a: {
        "method": "POST", "url": "https://api.linear.app/graphql",
        "headers": _linear_headers(), "json": a.get("payload", {}),
    }),
    "linear_list_issues": _http(lambda a: {
        "method": "POST", "url": "https://api.linear.app/graphql",
        "headers": _linear_headers(), "json": {"query": "{ issues { nodes { id title } } }"},
    }),
    "notion_create_page": _http(lambda a: {
        "method": "POST", "url": "https://api.notion.com/v1/pages",
        "headers": _notion_headers(), "json": a.get("payload", {}),
    }),
    "notion_query_db": _http(lambda a: {
        "method": "POST", "url": f"https://api.notion.com/v1/databases/{a.get('database_id', '')}/query",
        "headers": _notion_headers(), "json": a.get("filter", {}),
    }),
    "notion_update_block": _http(lambda a: {
        "method": "PATCH", "url": f"https://api.notion.com/v1/blocks/{a.get('block_id', '')}",
        "headers": _notion_headers(), "json": a.get("payload", {}),
    }),
    "shortcut_create_story": _http(lambda a: {
        "method": "POST", "url": "https://api.app.shortcut.com/api/v3/stories",
        "headers": _shortcut_headers(), "json": a.get("payload", {}),
    }),
    "shortcut_search": _http(lambda a: {
        "method": "GET", "url": "https://api.app.shortcut.com/api/v3/search/stories",
        "headers": _shortcut_headers(), "params": {"query": a.get("query", "")},
    }),

    # ── Analysis tools ──
    "sonar_scan": _make(lambda a: f"sonar-scanner -Dsonar.projectKey={_q(a.get('project', ''))} -Dsonar.host.url=$SONAR_HOST_URL -Dsonar.token=$SONAR_TOKEN 2>&1"),
    "sonar_get_issues": _http(lambda a: {
        "method": "GET", "url": f"{os.environ.get('SONAR_HOST_URL', '')}/api/issues/search",
        "headers": _sonar_headers(), "params": {"componentKeys": a.get("project", ""), "statuses": "OPEN"},
    }),
    "codecov_upload": _make(lambda a: f"codecov upload-process -t $CODECOV_TOKEN -f {_q(a.get('file', 'coverage.xml'))} 2>&1"),
    "codecov_get_report": _http(lambda a: {
        "method": "GET",
        "url": f"https://codecov.io/api/v2/github/{a.get('owner', '')}/repos/{a.get('repo', '')}/commits/{a.get('sha', '')}",
        "headers": _codecov_headers(),
    }),
    "snyk_test": _make(lambda a: f"snyk test --json {_q(a.get('path', '.'))} 2>&1"),
    "snyk_monitor": _make(lambda a: f"snyk monitor {_q(a.get('path', '.'))} 2>&1"),
    "snyk_container_test": _make(lambda a: f"snyk container test {_q(a.get('image', ''))} 2>&1"),

    # ── Cloud tools ──
    "aws_s3": _make(lambda a: f"aws s3 {_q(a.get('command', 'ls'))} {_q(a.get('args', ''))} 2>&1"),
    "aws_ecs": _make(lambda a: f"aws ecs {_q(a.get('command', 'list-services'))} {_q(a.get('args', ''))} 2>&1"),
    "aws_lambda": _make(lambda a: f"aws lambda {_q(a.get('command', 'list-functions'))} {_q(a.get('args', ''))} 2>&1"),
    "aws_cloudformation": _make(lambda a: f"aws cloudformation {_q(a.get('command', 'describe-stacks'))} {_q(a.get('args', ''))} 2>&1"),
    "aws_iam": _make(lambda a: f"aws iam {_q(a.get('command', 'list-roles'))} {_q(a.get('args', ''))} 2>&1"),
    "tf_init": _make(lambda a: f"terraform -chdir={_q(a.get('path', '.'))} init 2>&1"),
    "tf_plan": _make(lambda a: f"terraform -chdir={_q(a.get('path', '.'))} plan -out=tfplan 2>&1"),
    "tf_apply": _make(lambda a: f"terraform -chdir={_q(a.get('path', '.'))} apply -auto-approve tfplan 2>&1"),
    "tf_validate": _make(lambda a: f"terraform -chdir={_q(a.get('path', '.'))} validate 2>&1"),
    "tf_state": _make(lambda a: f"terraform -chdir={_q(a.get('path', '.'))} state {_q(a.get('command', 'list'))} 2>&1"),
    "kubectl_apply": _make(lambda a: f"kubectl apply -f {_q(a.get('file', ''))} {_q(a.get('args', ''))} 2>&1"),
    "kubectl_get": _make(lambda a: f"kubectl get {_q(a.get('resource', 'pods'))} {_q(a.get('args', ''))} 2>&1"),
    "kubectl_describe": _make(lambda a: f"kubectl describe {_q(a.get('resource', ''))} {_q(a.get('name', ''))} 2>&1"),
    "kubectl_logs": _make(lambda a: f"kubectl logs {_q(a.get('pod', ''))} {_q(a.get('args', '--tail=100'))} 2>&1"),
    "kubectl_rollout": _make(lambda a: f"kubectl rollout {_q(a.get('command', 'status'))} {_q(a.get('resource', ''))} 2>&1"),

    # ── Web search / research ──
    "web_search": _make(lambda a: "echo 'Web search requires MCP server. Configure in config/mcp_servers.yaml'"),
    "fetch_url": _http(lambda a: {"method": "GET", "url": a.get("url", ""), "headers": {}}),

    # ── GitHub Workflows ──
    "gh_workflow_run": _make(lambda a: f"gh workflow run {_q(a.get('workflow', ''))} {_q(a.get('args', ''))} 2>&1"),
    "gh_workflow_list": _make(lambda a: f"gh workflow list {_q(a.get('args', ''))} 2>&1"),
    "gh_run_view": _make(lambda a: f"gh run view {_q(a.get('run_id', ''))} {_q(a.get('args', ''))} 2>&1"),
    "gh_run_cancel": _make(lambda a: f"gh run cancel {_q(a.get('run_id', ''))} 2>&1"),
    "gh_actions_cache": _make(lambda a: f"gh actions-cache list {_q(a.get('args', ''))} 2>&1"),

    # ── Python Advanced ──
    "poetry_install": _make(lambda a: f"poetry install {_q(a.get('args', ''))} 2>&1"),
    "poetry_add": _make(lambda a: f"poetry add {_q(a.get('package', ''))} {_q(a.get('args', ''))} 2>&1"),
    "pip_compile": _make(lambda a: f"pip-compile {_q(a.get('input', 'requirements.in'))} -o {_q(a.get('output', 'requirements.txt'))} 2>&1"),
    "black_format": _make(lambda a: f"black {_q(a.get('path', '.'))} {_q(a.get('args', ''))} 2>&1"),
    "isort_fix": _make(lambda a: f"isort {_q(a.get('path', '.'))} {_q(a.get('args', ''))} 2>&1"),
    "pytest_cov": _make(lambda a: f"pytest {_q(a.get('path', 'tests/'))} --cov={_q(a.get('source', 'src'))} --cov-report=term-missing 2>&1"),
    "python_repl": _make(lambda a: f"python3 -c {_q(a.get('code', 'print(1)'))} 2>&1"),

    # ── TypeScript Advanced ──
    "pnpm_install": _make(lambda a: f"pnpm install {_q(a.get('args', ''))} 2>&1"),
    "pnpm_run": _make(lambda a: f"pnpm run {_q(a.get('script', ''))} {_q(a.get('args', ''))} 2>&1"),
    "tsx_run": _make(lambda a: f"npx tsx {_q(a.get('file', ''))} 2>&1"),
    "prettier_format": _make(lambda a: f"npx prettier --write {_q(a.get('path', 'src/'))} 2>&1"),
    "vitest_run": _make(lambda a: f"npx vitest run {_q(a.get('path', ''))} 2>&1"),

    # ── Material UI ──
    "mui_storybook": _make(lambda a: f"npx storybook build {_q(a.get('args', ''))} 2>&1"),
    "mui_theme_check": _make(lambda a: f"npx tsc --noEmit {_q(a.get('path', 'src/theme/'))} 2>&1"),

    # ── FastAPI Advanced ──
    "uvicorn_check": _http(lambda a: {"method": "GET", "url": f"http://localhost:{a.get('port', 8000)}/health", "headers": {}}),
    "fastapi_openapi_export": _make(lambda a: f"python3 -c {_q('from ' + a.get('app_module', 'main') + ' import app; import json; print(json.dumps(app.openapi(), indent=2))')} 2>&1"),

    # ── AWS Advanced ──
    "aws_ssm": _make(lambda a: f"aws ssm {_q(a.get('command', 'get-parameter'))} {_q(a.get('args', ''))} 2>&1"),
    "aws_secrets": _make(lambda a: f"aws secretsmanager {_q(a.get('command', 'list-secrets'))} {_q(a.get('args', ''))} 2>&1"),
    "aws_ecr": _make(lambda a: f"aws ecr {_q(a.get('command', 'describe-repositories'))} {_q(a.get('args', ''))} 2>&1"),
    "aws_sqs": _make(lambda a: f"aws sqs {_q(a.get('command', 'list-queues'))} {_q(a.get('args', ''))} 2>&1"),
    "aws_sns": _make(lambda a: f"aws sns {_q(a.get('command', 'list-topics'))} {_q(a.get('args', ''))} 2>&1"),
    "aws_dynamodb": _make(lambda a: f"aws dynamodb {_q(a.get('command', 'list-tables'))} {_q(a.get('args', ''))} 2>&1"),
    "aws_rds": _make(lambda a: f"aws rds {_q(a.get('command', 'describe-db-instances'))} {_q(a.get('args', ''))} 2>&1"),
    "aws_route53": _make(lambda a: f"aws route53 {_q(a.get('command', 'list-hosted-zones'))} {_q(a.get('args', ''))} 2>&1"),

    # ── React / Next.js Advanced ──
    "next_build": _make(lambda a: f"npx next build {_q(a.get('args', ''))} 2>&1"),
    "next_dev": _make(lambda a: f"npx next dev -p {_q(str(a.get('port', 3000)))} 2>&1 &"),
    "cra_test": _make(lambda a: f"npx react-scripts test --watchAll=false {_q(a.get('args', ''))} 2>&1"),
    "react_scripts_build": _make(lambda a: "npx react-scripts build 2>&1"),
}


# ---------------------------------------------------------------------------
# Tool lockdown: agents can ONLY use tools from BUILTIN_ADAPTERS + MCP
# ---------------------------------------------------------------------------

APPROVED_TOOLS: frozenset[str] = frozenset(BUILTIN_ADAPTERS.keys())


def is_approved_tool(tool_name: str) -> bool:
    """Check if a tool name is in the approved shared library.

    Agents can ONLY use tools that are registered here or via MCP config.
    This prevents agents from calling arbitrary commands or low-quality tools.
    """
    return tool_name in APPROVED_TOOLS


def register_builtins(tool_executor: Any) -> None:
    """Register all built-in tool adapters into a ToolExecutor instance.

    Only tools in BUILTIN_ADAPTERS are registered — agents cannot use
    arbitrary tools. MCP tools are registered separately via mcp_loader.
    """
    for tool_name, adapter in BUILTIN_ADAPTERS.items():
        tool_executor.register_adapter(tool_name, adapter)

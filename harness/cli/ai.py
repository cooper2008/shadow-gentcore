"""CLI entry point for the multi-domain agent framework.

Usage:
    ./ai --help
    ./ai domain init <name>
    ./ai agent create <domain> <name>
    ./ai run agent <agent_id> --task "..." --domain <path>
    ./ai run workflow <path> --task '{...}'
    ./ai learn <repo_path> --domain-name <name>
    ./ai tool list
    ./ai tool add <name>
    ./ai validate <path>
    ./ai certify <path>
    ./ai publish <path>
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import click


@click.group()
@click.version_option(version="0.1.0", prog_name="ai")
def cli() -> None:
    """Multi-domain AI agent framework CLI."""


# ── domain ────────────────────────────────────────────────────────────────


@cli.group()
def domain() -> None:
    """Domain management commands."""


@domain.command("register")
@click.argument("domain_path")
@click.option("--type", "dtype", default="external", type=click.Choice(["external", "example", "internal"]))
def domain_register(domain_path: str, dtype: str) -> None:
    """Register a domain in config/workspace.yaml.

    Example:
        ./ai domain register ./domains/payment --type external
    """
    import yaml as _yaml

    ws_path = Path(__file__).resolve().parent.parent.parent / "config" / "workspace.yaml"
    data: dict[str, Any] = {}
    if ws_path.exists():
        data = _yaml.safe_load(ws_path.read_text(encoding="utf-8")) or {}

    domains = data.get("domains", [])
    rel_path = domain_path

    for d in domains:
        if d.get("path") == rel_path:
            click.echo(f"Domain already registered: {rel_path}")
            return

    domains.append({"path": rel_path, "type": dtype})
    data["domains"] = domains
    ws_path.write_text(_yaml.dump(data, default_flow_style=False, sort_keys=False), encoding="utf-8")

    click.echo(f"Registered domain: {rel_path} (type: {dtype})")
    click.echo(f"Run './ai workspace' to verify.")


@domain.command("init")
@click.argument("name")
@click.option("--path", default=".", help="Directory to initialize the domain in")
@click.option("--owner", default="team", help="Domain owner")
def domain_init(name: str, path: str, owner: str) -> None:
    """Scaffold a new domain."""
    from harness.authoring.scaffolder import Scaffolder

    s = Scaffolder()
    result = s.scaffold_domain(name, Path(path), owner=owner)
    click.echo(f"Scaffolded domain '{name}' at {result['domain_dir']}")
    for f in result["files_created"]:
        click.echo(f"  created: {f}")


# ── pack ──────────────────────────────────────────────────────────────────


@cli.group()
def pack() -> None:
    """Capability pack management commands."""


@pack.command("create")
@click.argument("domain_name")
@click.argument("name")
def pack_create(domain_name: str, name: str) -> None:
    """Scaffold a new capability pack."""
    from harness.authoring.scaffolder import Scaffolder

    s = Scaffolder()
    result = s.scaffold_pack(Path(domain_name), name)
    click.echo(f"Created pack '{name}' at {result['pack_dir']}")


# ── agent ─────────────────────────────────────────────────────────────────


@cli.group()
def agent() -> None:
    """Agent management commands."""


@agent.command("create")
@click.argument("domain_name")
@click.argument("name")
@click.option("--category", default="reasoning", help="Agent category")
def agent_create(domain_name: str, name: str, category: str) -> None:
    """Scaffold a new agent manifest bundle."""
    from harness.authoring.scaffolder import Scaffolder

    s = Scaffolder()
    result = s.scaffold_agent(Path(domain_name), name, category=category)
    click.echo(f"Created agent '{name}' at {result['agent_dir']}")


# ── workflow ──────────────────────────────────────────────────────────────


@cli.group()
def workflow() -> None:
    """Workflow management commands."""


@workflow.command("create")
@click.argument("domain_name")
@click.argument("name")
def workflow_create(domain_name: str, name: str) -> None:
    """Scaffold a new workflow definition."""
    from harness.authoring.scaffolder import Scaffolder

    s = Scaffolder()
    result = s.scaffold_workflow(Path(domain_name), name)
    click.echo(f"Created workflow '{name}' at {result['workflow_file']}")


# ── validate / certify / publish ──────────────────────────────────────────


@cli.command()
@click.argument("path")
def validate(path: str) -> None:
    """Validate manifests at the given path."""
    from harness.authoring.validator import Validator

    v = Validator()
    result = v.validate_domain(Path(path))
    click.echo(result.summary)
    for e in result.errors:
        click.echo(f"  ERROR: [{e['rule']}] {e['message']}")
    for w in result.warnings:
        click.echo(f"  WARN:  [{w['rule']}] {w['message']}")
    if not result.is_valid:
        raise SystemExit(1)


@cli.command()
@click.argument("path")
def certify(path: str) -> None:
    """Certify a domain at the given path."""
    from harness.authoring.certifier import Certifier

    c = Certifier()
    result = c.certify_domain(Path(path))
    click.echo(result.summary)
    for note in result.notes:
        click.echo(f"  {note}")
    if not result.certified:
        raise SystemExit(1)


@cli.command()
@click.argument("path")
@click.option("--version", "ver", default="0.1.0", help="Version to publish")
@click.option("--owner", default="team", help="Domain owner")
def publish(path: str, ver: str, owner: str) -> None:
    """Publish a certified domain."""
    from harness.authoring.publisher import Publisher

    pub = Publisher()
    result = pub.publish(Path(path), version=ver, owner=owner)
    click.echo(f"Published {result['domain']}@{result['version']}")
    click.echo(f"  entry: {result['entry_path']}")


# ── run ───────────────────────────────────────────────────────────────────


@cli.group()
def run() -> None:
    """Run agents or workflows."""


def _make_provider(dry_run: bool, provider_config_path: str | None = None) -> Any:
    """Create the appropriate LLM provider.

    Priority:
    1. --dry-run → DryRunProvider (no API calls)
    2. provider_config_path → read model/provider from domain's provider.yaml
    3. ANTHROPIC_API_KEY set → AnthropicProvider (direct API)
    4. claude CLI available + logged in → ClaudeCodeProvider (subscription billing)
    5. Error: no provider available
    """
    if dry_run:
        from harness.providers.dry_run import DryRunProvider
        return DryRunProvider()

    # Read domain-level provider config (model selection, not credentials)
    provider_cfg: dict[str, Any] = {}
    if provider_config_path:
        cfg_path = Path(provider_config_path)
        if cfg_path.exists():
            import yaml as _yaml
            raw = _yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            provider_cfg = raw

    provider_name = provider_cfg.get("provider", "anthropic")
    model = provider_cfg.get("model", "claude-sonnet-4-20250514")
    max_tokens = int(provider_cfg.get("max_tokens", 8192))
    api_key_env = provider_cfg.get("api_key_env", "ANTHROPIC_API_KEY")

    if provider_name == "anthropic":
        api_key = os.environ.get(api_key_env, "")
        if api_key:
            from harness.providers.anthropic_provider import AnthropicProvider
            return AnthropicProvider(api_key=api_key, model=model, max_tokens=max_tokens)
    elif provider_name == "openai":
        api_key = os.environ.get(api_key_env, "")
        if api_key:
            from harness.providers.openai_provider import OpenAIProvider
            return OpenAIProvider(api_key=api_key, model=model, max_tokens=max_tokens)
    elif provider_name == "bedrock":
        from harness.providers.bedrock_provider import BedrockProvider
        return BedrockProvider(model=model)

    # Fallback: try ANTHROPIC_API_KEY directly
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if api_key:
        from harness.providers.anthropic_provider import AnthropicProvider
        return AnthropicProvider(api_key=api_key)

    # Last resort: Claude Code subscription
    import shutil
    if shutil.which("claude"):
        from harness.providers.claudecode_provider import ClaudeCodeProvider
        click.echo("  provider: Claude Code (subscription billing)")
        return ClaudeCodeProvider()

    click.echo(
        "Error: No LLM provider available.\n"
        "  Set ANTHROPIC_API_KEY for direct API, or\n"
        "  Install Claude Code CLI for subscription billing, or\n"
        "  Use --dry-run to test without an API.",
        err=True,
    )
    raise SystemExit(1)


@run.command("agent")
@click.argument("agent_id")
@click.option("--task", required=True, help="Task instruction for the agent")
@click.option("--domain", "domain_path", required=True, help="Path to the domain directory")
@click.option("--dry-run", is_flag=True, help="Run with mock provider (no API key needed)")
@click.option("--output", "output_format", default="pretty", type=click.Choice(["pretty", "json"]))
def run_agent(agent_id: str, task: str, domain_path: str, dry_run: bool, output_format: str) -> None:
    """Run a single agent by ID.

    Example:
        ./ai run agent backend_fastapi/FastAPICodeGenAgent/v1 \\
            --task "Add a GET /v1/health endpoint" \\
            --domain examples/backend_fastapi --dry-run
    """
    from harness.core.manifest_loader import ManifestLoader
    from harness.core.agent_runner import AgentRunner
    from harness.core.tool_executor import ToolExecutor
    from harness.tools.builtin import register_builtins
    from agent_contracts.contracts.task_envelope import TaskEnvelope

    loader = ManifestLoader()
    domain_root = Path(domain_path)
    domain_manifest = loader.load_domain(domain_root)

    # Resolve agent directory from agent_id (e.g. "backend_fastapi/FastAPICodeGenAgent/v1")
    parts = agent_id.split("/")
    project_root = Path(__file__).resolve().parent.parent.parent
    agent_dir = domain_root / "agents" / agent_id  # fallback

    if len(parts) >= 3:
        # Try: domain_root/agents/<AgentName>/<version>/
        candidate = domain_root / "agents" / parts[1] / parts[2]
        if candidate.exists():
            agent_dir = candidate
        else:
            # Try: project_root/agents/<domain>/<AgentName>/<version>/ (shared/internal agents)
            candidate2 = project_root / "agents" / parts[0] / parts[1] / parts[2]
            if candidate2.exists():
                agent_dir = candidate2
    elif len(parts) == 2:
        agent_dir = domain_root / "agents" / parts[0] / parts[1]

    if not agent_dir.exists():
        click.echo(f"Error: Agent directory not found: {agent_dir}", err=True)
        click.echo(f"  Searched: {domain_root / 'agents'} and {project_root / 'agents'}", err=True)
        raise SystemExit(1)

    manifest, system_prompt, context_items = loader.load_agent(
        agent_dir, domain_root, domain_manifest
    )

    provider = _make_provider(dry_run)
    tool_executor = ToolExecutor()
    register_builtins(tool_executor)
    runner = AgentRunner(provider=provider, tool_executor=tool_executor)

    task_envelope = TaskEnvelope(
        task_id=f"cli-{agent_id.replace('/', '-')}",
        agent_id=agent_id,
        input_payload={"instruction": task},
    )

    click.echo(f"Running agent: {agent_id}")
    if dry_run:
        click.echo("  mode: dry-run (no API calls)")
    click.echo(f"  task: {task}")
    click.echo()

    result = asyncio.run(runner.run(
        manifest=manifest,
        task=task_envelope,
        system_prompt_content=system_prompt,
        context_items=context_items,
    ))

    if output_format == "json":
        # Serialize result (RunRecord is not JSON-serializable directly)
        output = {
            "agent_id": agent_id,
            "status": result["run_record"].status.value,
            "content": result["result"].get("content", ""),
            "tokens_used": result["run_record"].tokens_used,
            "duration_ms": result["run_record"].duration_ms,
        }
        click.echo(json.dumps(output, indent=2))
    else:
        click.echo(f"Status: {result['run_record'].status.value}")
        click.echo(f"Tokens: {result['run_record'].tokens_used}")
        click.echo(f"Duration: {result['run_record'].duration_ms}ms")
        click.echo()
        content = result["result"].get("content", "")
        if content:
            click.echo(content)


@run.command("workflow")
@click.argument("workflow_path")
@click.option("--task", "task_json", default=None, help="Task input as JSON string")
@click.option("--dry-run", is_flag=True, help="Run with mock provider (no API calls)")
def run_workflow(workflow_path: str, task_json: str | None, dry_run: bool) -> None:
    """Run a workflow from a definition file.

    Example:
        ./ai run workflow examples/backend_fastapi/workflows/feature_endpoint.yaml \\
            --task '{"feature_description": "Add health endpoint"}' --dry-run
    """
    from harness.core.manifest_loader import ManifestLoader

    wf_file = Path(workflow_path)
    if not wf_file.suffix:
        wf_file = wf_file.with_suffix(".yaml")
    if not wf_file.exists():
        click.echo(f"Error: workflow file not found: {wf_file}", err=True)
        raise SystemExit(1)

    task_input: dict[str, Any] | None = None
    if task_json:
        try:
            task_input = json.loads(task_json)
        except json.JSONDecodeError as e:
            click.echo(f"Error: invalid JSON in --task: {e}", err=True)
            raise SystemExit(1)

    provider = _make_provider(dry_run)
    loader = ManifestLoader()
    engine, workflow_data, step_configs = loader.boot_engine(
        wf_file, provider=provider, task_input=task_input,
    )

    click.echo(f"Running workflow: {workflow_data['name']}")
    if dry_run:
        click.echo("  mode: dry-run (no API calls)")
    click.echo(f"  steps: {len(workflow_data['steps'])}")
    click.echo()

    result = asyncio.run(engine.execute_dag(workflow_data["steps"], step_configs))

    click.echo(f"Status: {result['status']}")
    click.echo(f"Steps completed: {len(result['step_results'])}")
    for step_name, step_result in result["step_results"].items():
        status = step_result.get("status", "unknown")
        output_preview = str(step_result.get("output", ""))[:100]
        click.echo(f"  {step_name}: {status}")
        if output_preview:
            click.echo(f"    output: {output_preview}...")
    click.echo()

    if result["status"] != "completed":
        click.echo(f"Workflow failed at step: {result.get('failed_step', 'unknown')}", err=True)
        if "error" in result:
            click.echo(f"  error: {result['error']}", err=True)
        raise SystemExit(1)


# ── tool ──────────────────────────────────────────────────────────────────


AGENT_TOOLS_PACKS = Path(__file__).resolve().parent.parent.parent.parent / "agent-tools" / "src" / "agent_tools" / "packs"


@cli.group()
def tool() -> None:
    """Tool pack management commands."""


@tool.command("list")
def tool_list() -> None:
    """List all available tool packs."""
    import yaml

    packs_dir = AGENT_TOOLS_PACKS
    if not packs_dir.exists():
        click.echo(f"Warning: packs directory not found at {packs_dir}", err=True)
        return

    click.echo("Available tool packs:\n")
    for yaml_file in sorted(packs_dir.rglob("*.yaml")):
        try:
            data = yaml.safe_load(yaml_file.read_text(encoding="utf-8")) or {}
            pack_id = data.get("id", yaml_file.stem)
            desc = data.get("description", "")
            tools = data.get("tools", [])
            tool_count = len(tools)
            rel_path = yaml_file.relative_to(packs_dir)
            setup = data.get("setup_instructions", "")
            click.echo(f"  {pack_id}")
            click.echo(f"    file: {rel_path}  |  tools: {tool_count}")
            if desc:
                click.echo(f"    {desc}")
            if setup:
                click.echo(f"    setup: {setup[:80]}...")
            click.echo()
        except Exception:
            pass


@tool.command("add")
@click.argument("name")
def tool_add(name: str) -> None:
    """Show setup instructions for a tool pack.

    Example:
        ./ai tool add github
        ./ai tool add jira
    """
    import yaml

    packs_dir = AGENT_TOOLS_PACKS
    # Search for matching pack
    candidates = list(packs_dir.rglob(f"{name}.yaml")) + list(packs_dir.rglob(f"*/{name}.yaml"))

    if not candidates:
        click.echo(f"Error: No tool pack found for '{name}'.", err=True)
        click.echo("Use './ai tool list' to see available packs.", err=True)
        raise SystemExit(1)

    pack_file = candidates[0]
    data = yaml.safe_load(pack_file.read_text(encoding="utf-8")) or {}
    pack_id = data.get("id", name)
    tools = data.get("tools", [])
    setup = data.get("setup_instructions", "No setup instructions provided.")

    click.echo(f"Tool pack: {pack_id}")
    click.echo(f"File: {pack_file}")
    click.echo()
    click.echo(f"Tools ({len(tools)}):")
    for t in tools:
        tid = t if isinstance(t, str) else t.get("id", "?")
        click.echo(f"  - {tid}")
    click.echo()
    click.echo("Setup instructions:")
    click.echo(f"  {setup}")
    click.echo()
    click.echo("To use in your agent manifest, add to tools section:")
    click.echo(f'  - name: <tool_name>')
    click.echo(f'    pack: "{pack_id}"')


@tool.command("create")
@click.argument("pack_name")
@click.argument("tool_names", nargs=-1, required=True)
@click.option("--category", default=None, help="Pack category directory (e.g., services, cloud, frontend)")
@click.option("--adapter", default="cli", type=click.Choice(["cli", "mcp", "http_api"]))
@click.option("--description", "desc", default="", help="Pack description")
@click.option("--setup", default="", help="Setup instructions")
def tool_create(pack_name: str, tool_names: tuple[str, ...], category: str | None, adapter: str, desc: str, setup: str) -> None:
    """Create a new tool pack with the specified tools.

    Examples:
        # Simple CLI tools
        ./ai tool create my_tools lint_check test_run --adapter cli

        # Service tools in a category
        ./ai tool create datadog dd_query dd_alert --category observability --adapter http_api

        # With description and setup
        ./ai tool create newrelic nr_query nr_deploy --category observability \\
            --description "New Relic APM tools" --setup "Set NEW_RELIC_API_KEY env var"
    """
    import yaml as _yaml

    packs_dir = AGENT_TOOLS_PACKS
    if category:
        target_dir = packs_dir / category
    else:
        target_dir = packs_dir
    target_dir.mkdir(parents=True, exist_ok=True)

    pack_id = f"toolpack://{category + '/' if category else ''}{pack_name}"
    pack_file = target_dir / f"{pack_name}.yaml"

    if pack_file.exists():
        click.echo(f"Pack already exists: {pack_file}", err=True)
        raise SystemExit(1)

    # Build pack YAML
    tools_list = []
    for t in tool_names:
        tools_list.append({
            "id": f"tool://{t}",
            "adapter_class": adapter,
            "timeout": 60,
            "output_normalization": "json" if adapter == "http_api" else "text",
            "audit_logging": adapter == "http_api",
        })

    pack_data: dict[str, Any] = {
        "id": pack_id,
        "version": "1.0.0",
        "description": desc or f"{pack_name} tools",
        "tools": tools_list,
        "default_policy": {"sandbox": False, "auth_mode": "none"},
    }
    if setup:
        pack_data["setup_instructions"] = setup

    pack_file.write_text(_yaml.dump(pack_data, default_flow_style=False, sort_keys=False), encoding="utf-8")

    click.echo(f"Created tool pack: {pack_id}")
    click.echo(f"  File: {pack_file}")
    click.echo(f"  Tools: {', '.join(tool_names)}")
    click.echo()
    click.echo("Next steps:")
    click.echo(f"  1. Add adapter commands in harness/tools/builtin.py:")
    for t in tool_names:
        click.echo(f'     "{t}": _make(lambda a: f"your_command {{a.get(\'arg\', \'\')}} 2>&1"),')
    click.echo(f"  2. Use in agent manifests:")
    click.echo(f'     pack: "{pack_id}"')


# ── learn ─────────────────────────────────────────────────────────────────


@cli.command()
@click.argument("repo_paths", nargs=-1, required=True)
@click.option("--domain-name", default=None, help="Name for the generated domain")
@click.option("--output-dir", default=".", help="Where to write the generated domain")
@click.option("--dry-run", is_flag=True, help="Run with mock provider (no API calls)")
@click.option("--focus", default=None, help="Focus areas: api,frontend,data,infra,devops,cicd")
def learn(repo_paths: tuple[str, ...], domain_name: str | None, output_dir: str, dry_run: bool, focus: str | None) -> None:
    """Scan one or more repositories and auto-generate a domain with agents.

    Examples:
        # Single repo
        ./ai learn /path/to/backend --domain-name payment_service --dry-run

        # Multiple repos (DevOps: templates + app + infra)
        ./ai learn /repos/base-templates /repos/app-config /repos/infra-iac \\
            --domain-name devops_platform --focus devops,cicd --dry-run
    """
    from harness.core.manifest_loader import ManifestLoader

    resolved_paths: list[str] = []
    for rp in repo_paths:
        repo = Path(rp).resolve()
        if not repo.exists():
            click.echo(f"Error: Repository path not found: {repo}", err=True)
            raise SystemExit(1)
        resolved_paths.append(str(repo))

    if domain_name is None:
        domain_name = Path(resolved_paths[0]).name

    project_root = Path(__file__).resolve().parent.parent.parent
    factory_workflow = project_root / "workflows" / "factory" / "learn_and_create.yaml"

    if not factory_workflow.exists():
        click.echo(f"Error: Factory workflow not found at {factory_workflow}", err=True)
        raise SystemExit(1)

    provider = _make_provider(dry_run)
    loader = ManifestLoader()

    task_input = {
        "repo_path": resolved_paths[0],              # backward compat
        "repo_paths": resolved_paths,                 # multi-repo
        "focus_areas": focus.split(",") if focus else [],
        "domain_name": domain_name,
        "output_dir": str(Path(output_dir).resolve()),
    }

    engine, workflow_data, step_configs = loader.boot_engine(
        factory_workflow,
        domain_root=project_root,  # so _factory/LearnAgent/v1 resolves under agents/
        provider=provider,
        task_input=task_input,
    )

    click.echo(f"Learning from: {', '.join(resolved_paths)}")
    click.echo(f"Domain name: {domain_name}")
    click.echo(f"Output: {output_dir}/{domain_name}")
    if dry_run:
        click.echo("Mode: dry-run (no API calls)")
    click.echo()

    result = asyncio.run(engine.execute_dag(workflow_data["steps"], step_configs))

    click.echo(f"Status: {result['status']}")
    for step_name, step_result in result["step_results"].items():
        status = step_result.get("status", "unknown")
        click.echo(f"  {step_name}: {status}")

    if result["status"] == "completed":
        click.echo(f"\nDomain generated. Next steps:")
        click.echo(f"  ./ai validate {output_dir}/{domain_name}")
        click.echo(f"  ./ai run workflow {output_dir}/{domain_name}/workflows/<name>.yaml --dry-run")


# ── mcp ───────────────────────────────────────────────────────────────────


@cli.group()
def mcp() -> None:
    """MCP server management commands."""


@mcp.command("list")
def mcp_list() -> None:
    """List configured MCP servers and their tools."""
    from harness.tools.mcp_loader import load_mcp_config

    servers = load_mcp_config()
    if not servers:
        click.echo("No MCP servers configured. Edit config/mcp_servers.yaml to add servers.")
        return

    click.echo("Configured MCP servers:\n")
    for server in servers:
        name = server.get("name", "?")
        desc = server.get("description", "")
        tools = server.get("tools", [])
        click.echo(f"  {name}")
        if desc:
            click.echo(f"    {desc}")
        click.echo(f"    command: {server.get('command', '?')}")
        click.echo(f"    tools ({len(tools)}):")
        for t in tools:
            tname = t.get("name", t) if isinstance(t, dict) else str(t)
            tdesc = t.get("description", "") if isinstance(t, dict) else ""
            click.echo(f"      - {tname}" + (f" — {tdesc}" if tdesc else ""))
        click.echo(f'    use in manifest: pack: "toolpack://mcp/{name}"')
        click.echo()


@mcp.command("add")
@click.argument("name")
@click.option("--command", "cmd", required=True, help="Command to launch the MCP server")
@click.option("--description", "desc", default="", help="Server description")
def mcp_add(name: str, cmd: str, desc: str) -> None:
    """Add a new MCP server to config/mcp_servers.yaml.

    Example:
        ./ai mcp add puppeteer --command "npx -y @anthropic/mcp-server-puppeteer"
    """
    import yaml as _yaml

    config_path = Path(__file__).resolve().parent.parent.parent / "config" / "mcp_servers.yaml"
    data: dict[str, Any] = {}
    if config_path.exists():
        data = _yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    servers = data.get("servers", [])
    for s in servers:
        if s.get("name") == name:
            click.echo(f"MCP server '{name}' already configured. Edit config/mcp_servers.yaml to modify.")
            return

    servers.append({
        "name": name,
        "command": cmd,
        "transport": "stdio",
        "description": desc or f"{name} MCP server",
        "tools": [],
    })
    data["servers"] = servers
    config_path.write_text(_yaml.dump(data, default_flow_style=False, sort_keys=False), encoding="utf-8")

    click.echo(f"Added MCP server '{name}'")
    click.echo(f"  command: {cmd}")
    click.echo(f"\nNext steps:")
    click.echo(f"  1. Edit config/mcp_servers.yaml to declare the server's tools")
    click.echo(f'  2. Use in agent manifests: pack: "toolpack://mcp/{name}"')


# ── workspace ─────────────────────────────────────────────────────────────


@cli.command("workspace")
def workspace_cmd() -> None:
    """Show workspace status — repos, domains, tool packs, MCP servers."""
    from harness.core.workspace import Workspace
    click.echo(Workspace().summary())


# ── genesis ──────────────────────────────────────────────────────────────


@cli.group()
def genesis() -> None:
    """Genesis agents — self-building domain factory."""


@genesis.command("build")
@click.option("--sources", "-s", multiple=True, required=False, help="Knowledge source paths (repos, docs)")
@click.option("--industry", "-i", default=None, help="Industry type (e.g. fintech, healthcare, devops)")
@click.option("--output", "-o", default="", help="Output directory (default: domain path)")
@click.option("--dry-run", is_flag=True, help="Run with mock provider (no API calls)")
@click.option("--team", "-t", default=None, help="Team name from workspace.yaml (framework dev mode)")
@click.option("--domain", "domain_path", default=None, help="Path to domain repo containing domain.yaml (standalone mode)")
@click.option("--discover", "-d", "discover_path", default=None, help="Directory to auto-discover sources")
def genesis_build(sources: tuple[str, ...], industry: str | None, output: str, dry_run: bool, team: str | None, domain_path: str | None, discover_path: str | None) -> None:
    """Run the full genesis pipeline to build a domain.

    Standalone mode (domain repo, no workspace.yaml needed):
        ./ai genesis build --domain .
        ./ai genesis build --domain /path/to/my-domain --dry-run

    Framework dev mode (registered in workspace.yaml):
        ./ai genesis build --team backend-team --dry-run

    Legacy modes:
        ./ai genesis build -s /path/to/repo -i fintech --dry-run
        ./ai genesis build --discover /path/to/project --dry-run
    """
    from harness.core.manifest_loader import ManifestLoader
    import yaml as _yaml

    project_root = Path(__file__).resolve().parent.parent.parent

    # Source resolution: domain_path (standalone) > team (workspace) > discover > explicit sources
    task_input: dict[str, Any] = {}
    provider_config_path: str | None = None

    if domain_path:
        # ── Standalone mode: reads domain.yaml directly, no workspace.yaml ──
        dp = Path(domain_path).resolve()
        domain_yaml = dp / "domain.yaml"
        if not domain_yaml.exists():
            click.echo(f"Error: domain.yaml not found in {dp}", err=True)
            click.echo("Make sure you're pointing at a directory with domain.yaml (cloned from gentcore-template).", err=True)
            raise SystemExit(1)

        domain_cfg = _yaml.safe_load(domain_yaml.read_text(encoding="utf-8")) or {}
        domain_name = domain_cfg.get("name", dp.name)
        industry = industry or domain_cfg.get("industry", "software")

        # Build team_config from domain.yaml fields
        team_config: dict[str, Any] = {
            "industry": industry,
            "trusted": True,
        }

        # Reference repos (optional, from domain.yaml)
        if "reference" in domain_cfg:
            refs = []
            for ref in domain_cfg["reference"]:
                if isinstance(ref, dict):
                    p = Path(ref["path"])
                    refs.append({"path": str(p if p.is_absolute() else (dp / p).resolve()), "label": ref.get("label", "")})
                else:
                    p = Path(ref)
                    refs.append({"path": str(p if p.is_absolute() else (dp / p).resolve()), "label": ""})
            team_config["reference"] = refs

        # Target repos — default to src/ and tests/ in the domain repo
        targets = []
        if "target" in domain_cfg:
            for t in domain_cfg["target"]:
                p = Path(t["path"] if isinstance(t, dict) else t)
                targets.append({"path": str(p if p.is_absolute() else (dp / p).resolve())})
        else:
            for subdir in ("src", "tests", "app", "lib"):
                candidate = dp / subdir
                if candidate.exists():
                    targets.append({"path": str(candidate)})
        if not targets:
            targets.append({"path": str(dp)})
        team_config["target"] = targets

        # Docs — default to docs/ and context/ if they exist
        docs = []
        if "docs" in domain_cfg:
            for d in domain_cfg["docs"]:
                p = Path(d["path"] if isinstance(d, dict) else d)
                docs.append({"path": str(p if p.is_absolute() else (dp / p).resolve()), "type": "documents"})
        else:
            for subdir in ("docs", "context", "documentation"):
                candidate = dp / subdir
                if candidate.exists() and any(candidate.rglob("*.md")):
                    docs.append({"path": str(candidate), "type": "documents"})
        if docs:
            team_config["docs"] = docs

        team_config["output"] = str(dp)
        task_input["team_config"] = team_config

        # Provider config from domain repo
        provider_yaml = dp / "config" / "provider.yaml"
        if provider_yaml.exists():
            provider_config_path = str(provider_yaml)

        output = output or str(dp)

    elif team:
        # Load team config from workspace.yaml
        ws_path = project_root / "config" / "workspace.yaml"
        if not ws_path.exists():
            click.echo("Error: config/workspace.yaml not found", err=True)
            raise SystemExit(1)
        ws = _yaml.safe_load(ws_path.read_text(encoding="utf-8")) or {}
        teams = ws.get("teams", {})
        if team not in teams:
            click.echo(f"Error: Team '{team}' not found in workspace.yaml", err=True)
            click.echo(f"Available teams: {', '.join(teams.keys())}", err=True)
            raise SystemExit(1)
        team_config = teams[team]
        # Resolve relative paths against workspace.yaml location
        for key in ("reference", "target", "docs"):
            items = team_config.get(key, [])
            for item in items:
                if isinstance(item, dict) and "path" in item:
                    p = Path(item["path"])
                    if not p.is_absolute():
                        item["path"] = str((project_root / p).resolve())
        task_input["team_config"] = team_config
        industry = industry or team_config.get("industry", "software")
        output = output or team_config.get("output", "./output")
        domain_name = team.replace("-", "_")
        tc = task_input.get("team_config", {})
        pcp = tc.get("provider_config")
        if pcp:
            provider_config_path = str(Path(pcp).resolve()) if not Path(pcp).is_absolute() else pcp

    elif discover_path:
        dp = Path(discover_path).resolve()
        if not dp.exists():
            click.echo(f"Error: Discovery path not found: {dp}", err=True)
            raise SystemExit(1)
        task_input["discover_path"] = str(dp)
        domain_name = dp.name
    elif sources:
        # Existing behavior — backward compat
        resolved_sources: list[dict[str, str]] = []
        for s in sources:
            src_path = Path(s).resolve()
            if not src_path.exists():
                click.echo(f"Error: Source path not found: {src_path}", err=True)
                raise SystemExit(1)
            resolved_sources.append({"path": str(src_path), "type": "git_repo"})
        task_input["sources"] = resolved_sources
        domain_name = Path(output).resolve().name
    else:
        click.echo("Error: Provide --domain <path>, --team <name>, --discover <path>, or --sources", err=True)
        click.echo("  Standalone (domain repo): ./ai genesis build --domain .", err=True)
        click.echo("  Framework dev:            ./ai genesis build --team my-team", err=True)
        raise SystemExit(1)

    task_input["industry"] = industry
    task_input["domain_name"] = domain_name

    output_dir = Path(output).resolve() if output else Path(domain_path).resolve() if domain_path else Path("./output").resolve()

    genesis_workflow = project_root / "workflows" / "genesis" / "genesis_build.yaml"

    if not genesis_workflow.exists():
        click.echo(f"Error: Genesis workflow not found at {genesis_workflow}", err=True)
        raise SystemExit(1)

    provider = _make_provider(dry_run, provider_config_path=provider_config_path)
    loader = ManifestLoader()

    engine, workflow_data, step_configs = loader.boot_engine(
        genesis_workflow,
        domain_root=project_root,
        provider=provider,
        task_input=task_input,
    )

    click.echo(f"Genesis build: {domain_name}")
    click.echo(f"  industry: {industry}")
    if domain_path:
        click.echo(f"  domain: {Path(domain_path).resolve()}")
        tc = task_input.get("team_config", {})
        click.echo(f"  reference repos: {len(tc.get('reference', []))}")
        click.echo(f"  target dirs: {len(tc.get('target', []))}")
        click.echo(f"  doc sources: {len(tc.get('docs', []))}")
    elif team:
        click.echo(f"  team: {team}")
        tc = task_input.get("team_config", {})
        click.echo(f"  reference repos: {len(tc.get('reference', []))}")
        click.echo(f"  target repos: {len(tc.get('target', []))}")
        click.echo(f"  doc sources: {len(tc.get('docs', []))}")
    elif discover_path:
        click.echo(f"  discover: {discover_path}")
    else:
        click.echo(f"  sources: {len(task_input.get('sources', []))}")
        for src in task_input.get("sources", []):
            click.echo(f"    - {src['path']}")
    click.echo(f"  output: {output_dir}")
    if dry_run:
        click.echo("  mode: dry-run (no API calls)")
    click.echo()

    result = asyncio.run(engine.execute_dag(workflow_data["steps"], step_configs))

    click.echo(f"Status: {result['status']}")
    click.echo(f"Steps completed: {len(result['step_results'])}")
    for step_name, step_result in result["step_results"].items():
        status = step_result.get("status", "unknown")
        output_preview = str(step_result.get("output", ""))[:100]
        click.echo(f"  {step_name}: {status}")
        if output_preview:
            click.echo(f"    output: {output_preview}...")
    click.echo()

    if result["status"] == "completed":
        click.echo("Genesis build complete. Next steps:")
        click.echo(f"  ./ai validate {output_dir}")
        click.echo(f"  ./ai run workflow {output_dir}/workflows/<name>.yaml --dry-run")
    else:
        click.echo(f"Genesis build failed at step: {result.get('failed_step', 'unknown')}", err=True)
        if "error" in result:
            click.echo(f"  error: {result['error']}", err=True)
        raise SystemExit(1)


@genesis.command("scan")
@click.option("--sources", "-s", multiple=True, required=False, help="Knowledge source paths to scan")
@click.option("--industry", "-i", default=None, help="Industry type (optional for scan)")
@click.option("--dry-run", is_flag=True, help="Run with mock provider (no API calls)")
@click.option("--team", "-t", default=None, help="Team name from workspace.yaml")
def genesis_scan(sources: tuple[str, ...], industry: str | None, dry_run: bool, team: str | None) -> None:
    """Quick scan only — inventory sources without building.

    Runs SourceScannerAgent + KnowledgeMapperAgent to produce an inventory
    and coverage report.

    Examples:
        ./ai genesis scan -s /path/to/repo --dry-run
        ./ai genesis scan --team backend-team --dry-run
    """
    from harness.core.manifest_loader import ManifestLoader
    import yaml as _yaml

    project_root = Path(__file__).resolve().parent.parent.parent
    task_input: dict[str, Any] = {}

    if team:
        # Load team config from workspace.yaml
        ws_path = project_root / "config" / "workspace.yaml"
        if not ws_path.exists():
            click.echo("Error: config/workspace.yaml not found", err=True)
            raise SystemExit(1)
        ws = _yaml.safe_load(ws_path.read_text(encoding="utf-8")) or {}
        teams = ws.get("teams", {})
        if team not in teams:
            click.echo(f"Error: Team '{team}' not found in workspace.yaml", err=True)
            click.echo(f"Available teams: {', '.join(teams.keys())}", err=True)
            raise SystemExit(1)
        team_config = teams[team]
        # Resolve relative paths against workspace.yaml location
        for key in ("reference", "target", "docs"):
            items = team_config.get(key, [])
            for item in items:
                if isinstance(item, dict) and "path" in item:
                    p = Path(item["path"])
                    if not p.is_absolute():
                        item["path"] = str((project_root / p).resolve())
        task_input["team_config"] = team_config
        if industry:
            task_input["industry"] = industry
        elif "industry" in team_config:
            task_input["industry"] = team_config["industry"]
    elif sources:
        resolved_sources: list[dict[str, str]] = []
        for s in sources:
            src_path = Path(s).resolve()
            if not src_path.exists():
                click.echo(f"Error: Source path not found: {src_path}", err=True)
                raise SystemExit(1)
            resolved_sources.append({"path": str(src_path), "type": "git_repo"})
        task_input["sources"] = resolved_sources
        if industry:
            task_input["industry"] = industry
    else:
        click.echo("Error: Provide --team or --sources", err=True)
        raise SystemExit(1)

    scan_workflow = project_root / "workflows" / "genesis" / "genesis_scan.yaml"

    if not scan_workflow.exists():
        click.echo(f"Error: Genesis scan workflow not found at {scan_workflow}", err=True)
        click.echo("Expected: workflows/genesis/genesis_scan.yaml", err=True)
        raise SystemExit(1)

    provider = _make_provider(dry_run)
    loader = ManifestLoader()

    engine, workflow_data, step_configs = loader.boot_engine(
        scan_workflow,
        domain_root=project_root,
        provider=provider,
        task_input=task_input,
    )

    if team:
        tc = task_input.get("team_config", {})
        click.echo(f"Genesis scan: team '{team}'")
        click.echo(f"  reference repos: {len(tc.get('reference', []))}")
        click.echo(f"  target repos: {len(tc.get('target', []))}")
        click.echo(f"  doc sources: {len(tc.get('docs', []))}")
    else:
        click.echo(f"Genesis scan: {len(task_input.get('sources', []))} source(s)")
        for src in task_input.get("sources", []):
            click.echo(f"  - {src['path']}")
    if dry_run:
        click.echo("  mode: dry-run (no API calls)")
    click.echo()

    result = asyncio.run(engine.execute_dag(workflow_data["steps"], step_configs))

    click.echo(f"Status: {result['status']}")
    for step_name, step_result in result["step_results"].items():
        status = step_result.get("status", "unknown")
        click.echo(f"  {step_name}: {status}")
        output_data = step_result.get("output", "")
        if output_data:
            click.echo(f"    {str(output_data)[:200]}")
    click.echo()


@genesis.command("test")
@click.option("--suite", default=None, help="Specific test suite to run")
@click.option("--agent", default=None, help="Specific agent to test")
def genesis_test(suite: str | None, agent: str | None) -> None:
    """Run golden tests for genesis agents.

    Example:
        ./ai genesis test
        ./ai genesis test --suite fintech
        ./ai genesis test --agent SourceScannerAgent
    """
    project_root = Path(__file__).resolve().parent.parent.parent
    golden_dir = project_root / "tests" / "golden"

    click.echo("Genesis golden tests")
    click.echo(f"  fixtures dir: {golden_dir}")
    if suite:
        click.echo(f"  suite filter: {suite}")
    if agent:
        click.echo(f"  agent filter: {agent}")
    click.echo()

    if not golden_dir.exists():
        click.echo(f"No golden test fixtures found at {golden_dir}")
        click.echo("Create test fixtures in tests/golden/ to enable genesis testing.")
        return

    # Discover test fixtures
    fixtures = sorted(golden_dir.rglob("*.yaml"))
    if suite:
        fixtures = [f for f in fixtures if suite in f.stem or suite in str(f.parent)]
    if agent:
        fixtures = [f for f in fixtures if agent.lower() in f.stem.lower()]

    if not fixtures:
        click.echo("No matching test fixtures found.")
        return

    click.echo(f"Found {len(fixtures)} test fixture(s):")
    for f in fixtures:
        rel = f.relative_to(golden_dir)
        click.echo(f"  - {rel}")
    click.echo()
    click.echo("Golden test execution not yet implemented.")
    click.echo("Run 'pytest harness/tests/ -k genesis' for unit tests.")


# ── serve ─────────────────────────────────────────────────────────────────


@cli.command("serve")
@click.option("--domain", "-d", default=".", show_default=True, help="Path to domain repo")
@click.option("--port", "-p", default=8765, show_default=True, help="Port to listen on")
@click.option("--host", default="0.0.0.0", show_default=True, help="Host to bind")
@click.option("--dry-run", is_flag=True, help="Use DryRunProvider (no API calls)")
def serve(domain: str, port: int, host: str, dry_run: bool) -> None:
    """Start the Agent API Server (HTTP interface to the engine).

    Exposes:
        GET  /health
        GET  /agents
        POST /run/agent
        POST /run/workflow

    Examples:
        ./ai serve --domain ../acme-backend --port 8765
        ./ai serve --domain . --dry-run
    """
    try:
        import uvicorn
    except ImportError:
        click.echo("Error: uvicorn is required. Install with: pip install 'shadow-gentcore[server]'", err=True)
        raise SystemExit(1)

    domain_path = str(Path(domain).resolve())
    if dry_run:
        os.environ["GENTCORE_DRY_RUN"] = "1"
    os.environ["DOMAIN_PATH"] = domain_path

    click.echo(f"Agent API Server")
    click.echo(f"  domain: {domain_path}")
    click.echo(f"  url:    http://{host}:{port}")
    if dry_run:
        click.echo("  mode:   dry-run (no API calls)")
    if os.environ.get("AGENT_API_KEY"):
        click.echo("  auth:   API key required")
    else:
        click.echo("  auth:   none (set AGENT_API_KEY to enable)")
    click.echo()

    from harness.server.app import create_app

    application = create_app(domain_path=domain_path)
    uvicorn.run(application, host=host, port=port, log_level="info")


@genesis.command("evolve")
@click.option("--domain", "-d", required=True, help="Path to the domain to evolve")
@click.option("--run-history", default=None, help="Path to run history for analysis")
@click.option("--dry-run", is_flag=True, help="Run with mock provider (no API calls)")
def genesis_evolve(domain: str, run_history: str | None, dry_run: bool) -> None:
    """Post-deployment evolution — improve an existing domain.

    Loads the genesis_evolve workflow and runs the EvolutionAgent to
    analyze and improve an existing domain based on run history.

    Example:
        ./ai genesis evolve --domain ./output/my_domain --dry-run
        ./ai genesis evolve --domain ./output/my_domain --run-history ./logs/runs.json
    """
    from harness.core.manifest_loader import ManifestLoader

    domain_path = Path(domain).resolve()
    if not domain_path.exists():
        click.echo(f"Error: Domain path not found: {domain_path}", err=True)
        raise SystemExit(1)

    project_root = Path(__file__).resolve().parent.parent.parent
    evolve_workflow = project_root / "workflows" / "genesis" / "genesis_evolve.yaml"

    if not evolve_workflow.exists():
        click.echo(f"Error: Genesis evolve workflow not found at {evolve_workflow}", err=True)
        raise SystemExit(1)

    provider = _make_provider(dry_run)
    loader = ManifestLoader()

    task_input: dict[str, Any] = {
        "domain_path": str(domain_path),
    }
    if run_history:
        history_path = Path(run_history).resolve()
        if not history_path.exists():
            click.echo(f"Error: Run history not found: {history_path}", err=True)
            raise SystemExit(1)
        task_input["run_history"] = str(history_path)

    engine, workflow_data, step_configs = loader.boot_engine(
        evolve_workflow,
        domain_root=project_root,
        provider=provider,
        task_input=task_input,
    )

    click.echo(f"Genesis evolve: {domain_path.name}")
    click.echo(f"  domain: {domain_path}")
    if run_history:
        click.echo(f"  run history: {run_history}")
    if dry_run:
        click.echo("  mode: dry-run (no API calls)")
    click.echo()

    result = asyncio.run(engine.execute_dag(workflow_data["steps"], step_configs))

    click.echo(f"Status: {result['status']}")
    click.echo(f"Steps completed: {len(result['step_results'])}")
    for step_name, step_result in result["step_results"].items():
        status = step_result.get("status", "unknown")
        output_preview = str(step_result.get("output", ""))[:100]
        click.echo(f"  {step_name}: {status}")
        if output_preview:
            click.echo(f"    output: {output_preview}...")
    click.echo()

    if result["status"] != "completed":
        click.echo(f"Evolution failed at step: {result.get('failed_step', 'unknown')}", err=True)
        if "error" in result:
            click.echo(f"  error: {result['error']}", err=True)
        raise SystemExit(1)


if __name__ == "__main__":
    cli()

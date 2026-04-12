"""Agent API Server — FastAPI app exposing the gentcore engine over HTTP.

Start with:
    ./ai serve --domain /path/to/domain --port 8765
    # or directly:
    uvicorn harness.server.app:create_app --factory --host 0.0.0.0 --port 8765

Environment variables:
    AGENT_API_KEY   — if set, all endpoints require Authorization: Bearer <key>
    DOMAIN_PATH     — default domain path (overridden per-request if provided)
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from harness.server.runner import list_agents, run_agent, run_workflow

# ── Auth ──────────────────────────────────────────────────────────────────

_bearer = HTTPBearer(auto_error=False)
_API_KEY = os.environ.get("AGENT_API_KEY", "")


def _check_auth(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> None:
    if not _API_KEY:
        return  # no key configured → open access
    if creds is None or creds.credentials != _API_KEY:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing API key")


# ── Request / Response models ─────────────────────────────────────────────


class RunAgentRequest(BaseModel):
    agent: str = Field(description="Agent ID, e.g. 'FastAPICodeGenAgent/v1'")
    task: str = Field(description="Task description for the agent")
    domain: str = Field(default="", description="Path to domain repo (defaults to DOMAIN_PATH env var)")
    dry_run: bool = Field(default=False)


class RunWorkflowRequest(BaseModel):
    workflow: str = Field(description="Workflow path, e.g. 'workflows/feature_delivery.yaml'")
    task: dict[str, Any] = Field(default_factory=dict, description="Task input dict")
    domain: str = Field(default="", description="Path to domain repo")
    dry_run: bool = Field(default=False)


class AgentInfo(BaseModel):
    id: str
    description: str = ""
    category: str = ""


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "0.1.0"


# ── App factory ───────────────────────────────────────────────────────────


def create_app(domain_path: str = "") -> FastAPI:
    """Create the FastAPI app, optionally pre-binding a default domain path."""
    _default_domain = domain_path or os.environ.get("DOMAIN_PATH", ".")

    app = FastAPI(
        title="Gentcore Agent API",
        description="HTTP interface to the shadow-gentcore agent engine.",
        version="0.1.0",
    )

    @app.get("/health", response_model=HealthResponse, tags=["meta"])
    async def health() -> HealthResponse:
        return HealthResponse()

    @app.get("/agents", response_model=list[AgentInfo], tags=["agents"], dependencies=[Depends(_check_auth)])
    async def agents_list(domain: str = "") -> list[AgentInfo]:
        """List available agents in the domain's agents/ directory."""
        d = domain or _default_domain
        return [AgentInfo(**a) for a in list_agents(d)]

    @app.post("/run/agent", tags=["run"], dependencies=[Depends(_check_auth)])
    async def run_agent_endpoint(req: RunAgentRequest) -> dict[str, Any]:
        """Run a single agent and return its output."""
        d = req.domain or _default_domain
        result = await run_agent(req.agent, req.task, d, dry_run=req.dry_run)
        if result.get("status") == "error":
            error_msg = result.get("error", "unknown error")
            code = 404 if "not found" in error_msg.lower() else 500
            raise HTTPException(status_code=code, detail=error_msg)
        return result

    @app.post("/run/workflow", tags=["run"], dependencies=[Depends(_check_auth)])
    async def run_workflow_endpoint(req: RunWorkflowRequest) -> dict[str, Any]:
        """Run a workflow and return the execution result."""
        d = req.domain or _default_domain
        result = await run_workflow(req.workflow, req.task, d, dry_run=req.dry_run)
        if result.get("status") == "error":
            raise HTTPException(status_code=500, detail=result.get("error", "unknown error"))
        return result

    return app


# Module-level app for uvicorn when DOMAIN_PATH is set via env
app = create_app()

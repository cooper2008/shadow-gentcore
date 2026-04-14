"""Agent API Server — FastAPI app exposing the gentcore engine over HTTP.

Start with:
    ./ai serve --domain /path/to/domain --port 8765
    # or directly:
    uvicorn harness.server.app:create_app --factory --host 0.0.0.0 --port 8765

Environment variables:
    AGENT_API_KEY        — if set, all endpoints require Authorization: Bearer <key>
    AGENT_AUTH_DISABLED  — set to "true" to disable auth (dev only)
    DOMAIN_PATH          — default domain path (overridden per-request if provided)
    AGENT_MAX_CONCURRENT — max concurrent agent/workflow executions (default: 10)
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import time
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from harness.core.composition_engine import HumanApprovalRequired
from harness.server.runner import list_agents, run_agent, run_workflow

logger = logging.getLogger(__name__)

# ── Concurrency ───────────────────────────────────────────────────────────────

_MAX_CONCURRENT = int(os.environ.get("AGENT_MAX_CONCURRENT", "10"))
_REQUEST_TIMEOUT = int(os.environ.get("AGENT_REQUEST_TIMEOUT", "300"))  # 5 min default

# ── Auth ──────────────────────────────────────────────────────────────────────

_bearer = HTTPBearer(auto_error=False)


def _check_auth(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> None:
    # Read at call time so key rotation takes effect without restart (Fix 3)
    api_key = os.environ.get("AGENT_API_KEY", "")
    if not api_key:
        # No API key configured — fail closed unless explicitly opted out
        if os.environ.get("AGENT_AUTH_DISABLED", "").lower() == "true":
            logger.warning("Authentication disabled via AGENT_AUTH_DISABLED. Do not use in production.")
            return
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API key not configured. Set AGENT_API_KEY or AGENT_AUTH_DISABLED=true for dev.",
        )
    if creds is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing API key")
    # Timing-safe comparison to prevent timing attacks
    token = creds.credentials if isinstance(creds.credentials, str) else str(creds.credentials)
    if not hmac.compare_digest(token, api_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing API key")


# ── Request / Response models ─────────────────────────────────────────────────


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


# ── App factory ───────────────────────────────────────────────────────────────


def create_app(domain_path: str = "") -> FastAPI:
    """Create the FastAPI app, optionally pre-binding a default domain path."""
    # Fix 2: resolve CWD at creation time instead of baking in "."
    _default_domain = domain_path or os.environ.get("DOMAIN_PATH", "")
    if not _default_domain:
        _default_domain = str(Path.cwd().resolve())
        logger.warning("DOMAIN_PATH not set, using current directory: %s", _default_domain)

    # Fix 1: per-app semaphore so each factory call gets its own limit
    _semaphore = asyncio.Semaphore(_MAX_CONCURRENT)

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
        try:
            await asyncio.wait_for(_semaphore.acquire(), timeout=0.1)
        except asyncio.TimeoutError:
            raise HTTPException(status_code=429, detail="Too many concurrent requests")
        try:
            d = req.domain or _default_domain
            try:
                result = await asyncio.wait_for(
                    run_agent(req.agent, req.task, d, dry_run=req.dry_run),
                    timeout=_REQUEST_TIMEOUT,
                )
            except asyncio.TimeoutError:
                raise HTTPException(status_code=504, detail="Request timed out")
            if result.get("status") == "error":
                error_msg = result.get("error", "unknown error")
                code = 404 if "not found" in error_msg.lower() else 500
                raise HTTPException(status_code=code, detail=error_msg)
            return result
        finally:
            _semaphore.release()

    @app.post("/run/workflow", tags=["run"], dependencies=[Depends(_check_auth)])
    async def run_workflow_endpoint(req: RunWorkflowRequest) -> dict[str, Any]:
        """Run a workflow and return the execution result."""
        try:
            await asyncio.wait_for(_semaphore.acquire(), timeout=0.1)
        except asyncio.TimeoutError:
            raise HTTPException(status_code=429, detail="Too many concurrent requests")
        try:
            d = req.domain or _default_domain
            try:
                result = await asyncio.wait_for(
                    run_workflow(req.workflow, req.task, d, dry_run=req.dry_run),
                    timeout=_REQUEST_TIMEOUT,
                )
            except asyncio.TimeoutError:
                raise HTTPException(status_code=504, detail="Request timed out")
            except HumanApprovalRequired as e:
                return JSONResponse(
                    status_code=202,
                    content={
                        "status": "awaiting_approval",
                        "workflow_id": e.workflow_id,
                        "step_name": e.step_name,
                        "message": e.message,
                        "approve_url": f"/workflows/{e.workflow_id}/approve/{e.step_name}",
                    },
                )
            if result.get("status") == "error":
                raise HTTPException(status_code=500, detail=result.get("error", "unknown error"))
            return result
        finally:
            _semaphore.release()

    @app.get("/workflows/{workflow_id}/approvals", tags=["approvals"], dependencies=[Depends(_check_auth)])
    async def list_approvals(workflow_id: str, domain: str = "") -> list[dict[str, Any]]:
        """List pending approvals for a workflow."""
        from harness.core.workflow_state import FileStateStore
        d = domain or _default_domain
        store = FileStateStore(Path(d) / ".gentcore" / "state")
        completed = store.list_completed(workflow_id)
        approvals = []
        for step in completed:
            if step.startswith("_approval_"):
                data = store.load_step(workflow_id, step)
                if data and data.get("status") == "pending":
                    approvals.append(data)
        return approvals

    @app.post("/workflows/{workflow_id}/approve/{step_name}", tags=["approvals"], dependencies=[Depends(_check_auth)])
    async def approve_step(workflow_id: str, step_name: str, domain: str = "") -> dict[str, Any]:
        """Approve a pending step, allowing the workflow to resume."""
        from harness.core.workflow_state import FileStateStore
        d = domain or _default_domain
        store = FileStateStore(Path(d) / ".gentcore" / "state")
        approval_key = f"_approval_{step_name}"
        data = store.load_step(workflow_id, approval_key)
        if not data:
            raise HTTPException(status_code=404, detail=f"No pending approval for {step_name}")
        if data.get("status") != "pending":
            raise HTTPException(status_code=409, detail=f"Approval already {data.get('status')}")
        data["status"] = "approved"
        data["approved_at"] = time.time()
        store.save_step(workflow_id, approval_key, data)
        return {"status": "approved", "workflow_id": workflow_id, "step_name": step_name}

    return app


# Module-level app for uvicorn when DOMAIN_PATH is set via env
app = create_app()

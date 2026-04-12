"""LocalFilesystemStorage — StorageBackend implementation backed by the local filesystem."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from agent_contracts.contracts.artifact_record import ArtifactRecord
from agent_contracts.contracts.checkpoint import Checkpoint
from agent_contracts.contracts.run_record import RunRecord
from agent_contracts.contracts.storage import StorageBackend


class LocalFilesystemStorage(StorageBackend):
    """Filesystem-backed StorageBackend for local development and testing.

    Directory layout:
        .harness/runs/<trace_id>/
            run_record.json
            artifacts/
                <artifact_id>.json
            checkpoints/
                <workflow_id>__<step>.json
    """

    def __init__(self, base_dir: str | Path = ".harness/runs") -> None:
        self._base_dir = Path(base_dir)

    # ------------------------------------------------------------------
    # StorageBackend implementation
    # ------------------------------------------------------------------

    async def save_artifact(self, record: ArtifactRecord) -> None:
        """Persist an artifact record to disk."""
        await asyncio.to_thread(self._sync_save_artifact, record)

    async def load_artifact(self, artifact_id: str) -> ArtifactRecord:
        """Retrieve an artifact record by ID, searching all run directories."""
        return await asyncio.to_thread(self._sync_load_artifact, artifact_id)

    async def save_checkpoint(self, checkpoint: Checkpoint) -> None:
        """Persist a checkpoint to disk under its workflow directory."""
        await asyncio.to_thread(self._sync_save_checkpoint, checkpoint)

    async def load_checkpoint(self, run_id: str, step_id: str) -> Checkpoint:
        """Retrieve the checkpoint for a specific run and step."""
        return await asyncio.to_thread(self._sync_load_checkpoint, run_id, step_id)

    async def save_run_record(self, record: RunRecord) -> None:
        """Persist a RunRecord to disk."""
        await asyncio.to_thread(self._sync_save_run_record, record)

    async def query_run_records(self, domain_id: str, **filters: Any) -> list[RunRecord]:
        """Query run records matching domain_id and optional filters."""
        return await asyncio.to_thread(self._sync_query_run_records, domain_id, **filters)

    async def list_artifacts(self, run_id: str) -> list[ArtifactRecord]:
        """List all artifacts recorded under a run directory."""
        return await asyncio.to_thread(self._sync_list_artifacts, run_id)

    # ------------------------------------------------------------------
    # Synchronous helpers (called via asyncio.to_thread)
    # ------------------------------------------------------------------

    def _run_dir(self, trace_id: str) -> Path:
        return self._base_dir / trace_id

    def _sync_save_artifact(self, record: ArtifactRecord) -> None:
        # Artifacts are stored per-run using the path embedded in the record.
        # If the path is relative, it is placed under .harness/runs/<path>.
        art_path = Path(record.path)
        if not art_path.is_absolute():
            art_path = self._base_dir / art_path
        art_path.parent.mkdir(parents=True, exist_ok=True)
        art_path.write_text(record.model_dump_json(indent=2), encoding="utf-8")

    def _sync_load_artifact(self, artifact_id: str) -> ArtifactRecord:
        if not self._base_dir.exists():
            raise FileNotFoundError(f"Artifact {artifact_id!r} not found")
        for run_dir in self._base_dir.iterdir():
            art_dir = run_dir / "artifacts"
            candidate = art_dir / f"{artifact_id}.json"
            if candidate.exists():
                return ArtifactRecord.model_validate_json(candidate.read_text(encoding="utf-8"))
        raise FileNotFoundError(f"Artifact {artifact_id!r} not found")

    def _sync_save_checkpoint(self, checkpoint: Checkpoint) -> None:
        cp_dir = self._base_dir / checkpoint.workflow_id / "checkpoints"
        cp_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{checkpoint.workflow_id}__{checkpoint.step}.json"
        (cp_dir / filename).write_text(checkpoint.model_dump_json(indent=2), encoding="utf-8")

    def _sync_load_checkpoint(self, run_id: str, step_id: str) -> Checkpoint:
        filename = f"{run_id}__{step_id}.json"
        path = self._base_dir / run_id / "checkpoints" / filename
        if not path.exists():
            raise FileNotFoundError(f"Checkpoint {run_id}/{step_id} not found")
        return Checkpoint.model_validate_json(path.read_text(encoding="utf-8"))

    def _sync_save_run_record(self, record: RunRecord) -> None:
        run_dir = self._run_dir(record.trace_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "run_record.json").write_text(record.model_dump_json(indent=2), encoding="utf-8")

    def _sync_query_run_records(self, domain_id: str, **filters: Any) -> list[RunRecord]:
        results: list[RunRecord] = []
        if not self._base_dir.exists():
            return results
        for trace_dir in self._base_dir.iterdir():
            if not trace_dir.is_dir():
                continue
            record_path = trace_dir / "run_record.json"
            if not record_path.exists():
                continue
            try:
                record = RunRecord.model_validate_json(record_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            record_dict = record.model_dump()
            if record_dict.get("metadata", {}).get("domain_id") != domain_id:
                continue
            if all(record_dict.get(k) == v for k, v in filters.items()):
                results.append(record)
        return results

    def _sync_list_artifacts(self, run_id: str) -> list[ArtifactRecord]:
        art_dir = self._run_dir(run_id) / "artifacts"
        if not art_dir.exists():
            return []
        records: list[ArtifactRecord] = []
        for p in art_dir.glob("*.json"):
            try:
                records.append(ArtifactRecord.model_validate_json(p.read_text(encoding="utf-8")))
            except Exception:
                continue
        return records

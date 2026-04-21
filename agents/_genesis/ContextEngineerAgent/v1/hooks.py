"""Post-execute hook for ContextEngineerAgent.

When the agent emits the new `reference_chunks` shape (Tier 2 memory), this
hook writes each chunk body to disk as `context/reference/chunks/<id>.md`
and generates the `context/reference_index.yaml` keyword index that the
runtime `context_retrieve` tool searches.

Flow:
    ContextEngineerAgent.run() emits output.documents.reference_chunks = [
      {id, topic, keywords, summary, content, depth_score}, ...
    ]
       ↓
    post_execute() writes:
      {domain_root}/context/reference/chunks/<id>.md          ← chunk body
      {domain_root}/context/reference_index.yaml              ← searchable index
       ↓
    Domain agents call context_retrieve(topic, keywords) → loads top-N chunks
    via the index, not the whole 20KB monolithic reference_docs.

Back-compat: the old `reference_docs` array path is untouched — downstream
Builder continues to write monolithic `context/reference/<filename>.md`
files for any entries there. Both shapes coexist.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml


def _resolve_output_dict(result: Any) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None
    for key in ("output", "parsed_output"):
        val = result.get(key)
        if isinstance(val, dict):
            return val
    content = result.get("content")
    if isinstance(content, str) and content.strip().startswith("{"):
        try:
            parsed = json.loads(content)
        except (ValueError, TypeError):
            return None
        if isinstance(parsed, dict):
            return parsed
    return None


def _resolve_domain_root(task: Any) -> str:
    if isinstance(task, dict):
        for key in ("domain_path", "output_dir"):
            val = task.get(key)
            if val:
                return str(val)
            inner = task.get("input_payload")
            if isinstance(inner, dict) and inner.get(key):
                return str(inner[key])
    return "."


_SLUG_RE = re.compile(r"[^A-Za-z0-9_\-]")


def _slugify(value: str, fallback: str) -> str:
    slug = _SLUG_RE.sub("_", (value or "").strip().lower()).strip("_")
    return slug or fallback


def post_execute(manifest: Any, task: Any, result: Any) -> Any:
    """Write reference chunks + index when the new Tier-2 shape is present."""
    output = _resolve_output_dict(result)
    if not output:
        return result

    documents = output.get("documents") or {}
    chunks = documents.get("reference_chunks") if isinstance(documents, dict) else None
    if not isinstance(chunks, list) or not chunks:
        # Agent emitted legacy shape or nothing — no-op.
        return result

    domain_root = Path(_resolve_domain_root(task)).expanduser().resolve()
    chunks_dir = domain_root / "context" / "reference" / "chunks"

    index_entries: list[dict[str, Any]] = []
    written: list[str] = []
    failed: list[dict[str, str]] = []

    for idx, entry in enumerate(chunks):
        if not isinstance(entry, dict):
            continue
        raw_id = str(entry.get("id", "")).strip() or f"chunk_{idx}"
        chunk_id = _slugify(raw_id, f"chunk_{idx}")
        topic = str(entry.get("topic", "")).strip()
        summary = str(entry.get("summary", "")).strip()[:200]
        body = entry.get("content", "")
        if not isinstance(body, str) or not body.strip():
            failed.append({"id": chunk_id, "error": "missing or empty content"})
            continue

        kws_raw = entry.get("keywords") or []
        if isinstance(kws_raw, str):
            kws_raw = [k.strip() for k in kws_raw.split(",") if k.strip()]
        keywords = [str(k).strip() for k in kws_raw if str(k).strip()]

        chunk_path = chunks_dir / f"{chunk_id}.md"
        try:
            chunk_path.parent.mkdir(parents=True, exist_ok=True)
            # Prepend frontmatter so humans browsing the file can still read it.
            frontmatter = (
                f"---\n"
                f"id: {chunk_id}\n"
                f"topic: {topic}\n"
                f"keywords: {keywords}\n"
                f"summary: {summary}\n"
                f"---\n\n"
            )
            chunk_path.write_text(frontmatter + body, encoding="utf-8")
            written.append(str(chunk_path))
        except Exception as exc:
            failed.append({"id": chunk_id, "error": str(exc)[:200]})
            continue

        rel_path = f"reference/chunks/{chunk_id}.md"
        index_entries.append({
            "id": chunk_id,
            "topic": topic,
            "keywords": keywords,
            "summary": summary,
            "path": rel_path,
            "size_bytes": len(body),
        })

    # Write the index only if we wrote at least one chunk.
    if index_entries:
        index_path = domain_root / "context" / "reference_index.yaml"
        try:
            index_path.parent.mkdir(parents=True, exist_ok=True)
            index_path.write_text(
                yaml.safe_dump(
                    {"version": 1, "chunks": index_entries},
                    sort_keys=False,
                    default_flow_style=False,
                ),
                encoding="utf-8",
            )
            written.append(str(index_path))
        except Exception as exc:
            failed.append({"id": "reference_index.yaml", "error": str(exc)[:200]})

    # Surface chunk-write stats on the result for gate / observability.
    if isinstance(result, dict):
        existing = result.get("output") or {}
        if not isinstance(existing, dict):
            existing = {"original": existing}
        existing.setdefault("tier2", {})
        existing["tier2"].update({
            "chunks_written": len(index_entries),
            "chunk_paths": written,
            "chunk_failures": failed,
        })
        result["output"] = existing
    return result

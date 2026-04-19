"""RunbookLoader — parse + index domain runbooks under context/runbooks/.

Supports the B7 runbook convention: markdown files with optional YAML frontmatter
carrying `id`, `triggers`, `estimated_duration`, `blast_radius`,
`approval_required`, `tags`, and `related_runbooks`.

Used by:
- TriageAgent to route signals to matching runbooks via `triggers`
- ExecuteAgent to load a runbook body as its step list
- RetrieveAgent to run free-text search across runbooks
- `toolpack://core/runbook_retrieval` (agent-tools) exposes this to agents

Schema documented in `config/runbook_schema.md`.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)


@dataclass
class Runbook:
    """A parsed runbook file."""

    id: str
    path: Path
    body: str
    triggers: list[str] = field(default_factory=list)
    estimated_duration: str | None = None
    blast_radius: str | None = None
    approval_required: bool = False
    tags: list[str] = field(default_factory=list)
    related_runbooks: list[str] = field(default_factory=list)
    raw_frontmatter: dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchHit:
    """A single retrieval hit for `RunbookLibrary.search_by_query`."""

    runbook_id: str
    source_path: Path
    excerpt: str
    relevance_score: float
    line_start: int = 0
    line_end: int = 0


def parse_runbook(path: Path | str) -> Runbook | None:
    """Parse a single runbook file. Returns None on unrecoverable errors."""
    p = Path(path)
    if not p.is_file():
        return None
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return None

    fm, body = _split_frontmatter(text)
    if fm is None and body is None:
        return None

    body_text = body if body is not None else text
    frontmatter = fm if fm is not None else {}

    rb_id = str(frontmatter.get("id") or p.stem)
    triggers = list(frontmatter.get("triggers") or [])
    tags = list(frontmatter.get("tags") or [])
    related = list(frontmatter.get("related_runbooks") or [])

    return Runbook(
        id=rb_id,
        path=p,
        body=body_text,
        triggers=[str(t) for t in triggers],
        estimated_duration=frontmatter.get("estimated_duration"),
        blast_radius=frontmatter.get("blast_radius"),
        approval_required=bool(frontmatter.get("approval_required", False)),
        tags=[str(t) for t in tags],
        related_runbooks=[str(r) for r in related],
        raw_frontmatter=frontmatter,
    )


def _split_frontmatter(text: str) -> tuple[dict[str, Any] | None, str | None]:
    """Return (frontmatter_dict, body) or (None, text) if no frontmatter.

    Malformed frontmatter is degraded to (empty-dict, raw_text) so the caller
    still gets a usable runbook.
    """
    if not text.startswith("---\n"):
        return None, text

    m = _FRONTMATTER_RE.match(text)
    if not m:
        # Opening `---` without matching close — treat entire file as body
        return {}, text

    raw_fm, body = m.group(1), m.group(2)
    try:
        fm = yaml.safe_load(raw_fm) or {}
        if not isinstance(fm, dict):
            fm = {}
    except yaml.YAMLError as exc:
        logger.debug("Malformed frontmatter ignored: %s", exc)
        fm = {}
    return fm, body


@dataclass
class RunbookLibrary:
    """Indexed collection of runbooks loaded from a directory.

    Instantiate via `RunbookLibrary.from_directory(path)`.
    """

    runbooks: list[Runbook] = field(default_factory=list)

    @classmethod
    def from_directory(cls, directory: Path | str) -> "RunbookLibrary":
        d = Path(directory)
        if not d.is_dir():
            return cls(runbooks=[])

        runbooks: list[Runbook] = []
        for md in sorted(d.rglob("*.md")):
            rb = parse_runbook(md)
            if rb is not None:
                runbooks.append(rb)
        return cls(runbooks=runbooks)

    def get_by_id(self, runbook_id: str) -> Runbook | None:
        for rb in self.runbooks:
            if rb.id == runbook_id:
                return rb
        return None

    def search_by_triggers(self, triggers: list[str]) -> list[Runbook]:
        """Return runbooks whose `triggers:` list intersects the input (union match)."""
        wanted = set(triggers)
        return [rb for rb in self.runbooks if wanted.intersection(rb.triggers)]

    def search_by_query(
        self,
        query: str,
        max_results: int = 5,
    ) -> list[SearchHit]:
        """Free-text keyword search across runbook bodies.

        Simple keyword frequency + first-match excerpt. Adequate for the small
        per-domain runbook libraries typical in practice; swap for an embedding
        model later without changing the interface.
        """
        if not query.strip() or max_results <= 0:
            return []

        query_terms = [t.lower() for t in re.findall(r"\w+", query) if t]
        if not query_terms:
            return []

        hits: list[SearchHit] = []
        for rb in self.runbooks:
            body_lower = rb.body.lower()
            total_term_hits = sum(body_lower.count(t) for t in query_terms)
            if total_term_hits == 0:
                continue
            excerpt, line_start, line_end = self._pick_excerpt(rb, query_terms)
            # Relevance: term hits normalised by body length (capped 1.0)
            score = min(1.0, total_term_hits / max(1, len(body_lower) / 200))
            hits.append(
                SearchHit(
                    runbook_id=rb.id,
                    source_path=rb.path,
                    excerpt=excerpt,
                    relevance_score=score,
                    line_start=line_start,
                    line_end=line_end,
                )
            )

        hits.sort(key=lambda h: h.relevance_score, reverse=True)
        return hits[:max_results]

    @staticmethod
    def _pick_excerpt(
        rb: Runbook,
        query_terms: list[str],
        context_lines: int = 2,
    ) -> tuple[str, int, int]:
        """Return (excerpt, line_start, line_end) around the first term match."""
        lines = rb.body.splitlines()
        for i, line in enumerate(lines):
            low = line.lower()
            if any(t in low for t in query_terms):
                start = max(0, i - context_lines)
                end = min(len(lines), i + context_lines + 1)
                excerpt = "\n".join(lines[start:end])
                return excerpt, start + 1, end
        # Fallback: first non-empty lines
        first = lines[:5]
        return "\n".join(first), 1, len(first)

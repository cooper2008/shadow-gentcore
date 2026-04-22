"""Tier 2 — warm-chunk retrieval for domain agents.

Replaces "domain agent loads entire reference/fastapi_patterns.md (20 KB)
to answer a 2-paragraph question" with "give me the 3 most relevant chunks
for this topic." Reduces typical reference lookup from 5-8 KB to 500-1500
tokens while keeping retrieval keyword-transparent and embedding-free.

Build-time shape produced by ContextEngineerAgent:
    <domain>/
      context/
        standards.md
        reference_index.yaml      ← SEARCHABLE INDEX (Tier 2 queries this)
        reference/
          chunks/
            <chunk_id>.md         ← chunk body (Tier 2 returns these)

reference_index.yaml:
    version: 1
    chunks:
      - id: fastapi_routers
        topic: "FastAPI router patterns"
        keywords: [router, APIRouter, endpoint, prefix, tag]
        summary: "How routers organize endpoints and compose into the main app."
        path: reference/chunks/fastapi_routers.md
        size_bytes: 2400

Runtime flow:
    tool: context_retrieve(topic="router", keywords=["prefix", "tag"], top_k=3)
      ↓
    ContextRetriever.search() — keyword overlap + topic-substring + inverse
    rarity scoring over reference_index.yaml; returns top-K ChunkRef.
      ↓
    tool opens each chunk path, returns its markdown body.

Zero embeddings, zero external deps (yaml already required). Scoring is
fully deterministic so results are reproducible + auditable.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


def _phrase_contained(container: str, phrase: str) -> bool:
    """True if `phrase` appears in `container` on word boundaries.

    Prevents `auth` from matching `oauth` (no word-boundary between
    'o' and 'a'). Reviewers flagged the previous direction-agnostic
    substring check as over-rewarding short generic queries.
    """
    phrase = phrase.strip()
    if not phrase or not container:
        return False
    # Bounded by non-word OR start/end of string
    pattern = r"(?:\A|\W)" + re.escape(phrase) + r"(?:\Z|\W)"
    return bool(re.search(pattern, container))


@dataclass(frozen=True)
class ChunkRef:
    id: str
    topic: str
    summary: str
    path: str
    keywords: tuple[str, ...]
    size_bytes: int = 0

    def score_for(self, topic: str, keywords: tuple[str, ...], doc_freq: dict[str, int]) -> float:
        """Relevance score for a (topic, keywords) query.

        - Topic phrase containment (word-bounded, both directions) ⇒ +2.0
          The phrase must be ≥3 chars AND appear on word boundaries
          (prevents `auth` from +2.0-matching `oauth token refresh`).
        - Topic word overlap ⇒ +1.0 per meaningful (>2 char) word
        - Keyword overlap with inverse-rarity weighting ⇒ +1/log(1+df) per match
        - Size penalty for chunks >5KB so grab-bags don't dominate.
        """
        score = 0.0
        topic_lc = topic.strip().lower()
        chunk_topic_lc = self.topic.lower()
        if topic_lc and len(topic_lc) >= 3 and len(chunk_topic_lc) >= 3:
            # Word-bounded phrase containment (either direction).
            if _phrase_contained(chunk_topic_lc, topic_lc) or _phrase_contained(topic_lc, chunk_topic_lc):
                score += 2.0
        topic_words = {w for w in re.findall(r"\w+", topic_lc) if len(w) > 2}
        chunk_topic_words = set(re.findall(r"\w+", chunk_topic_lc))
        score += 1.0 * len(topic_words & chunk_topic_words)

        chunk_kw_lc = {k.lower() for k in self.keywords}
        for kw in keywords:
            kw_lc = kw.lower()
            if kw_lc in chunk_kw_lc:
                df = doc_freq.get(kw_lc, 1)
                score += 1.0 / math.log1p(df)
            # Partial: keyword appears in topic or summary
            elif kw_lc in chunk_topic_lc or kw_lc in self.summary.lower():
                score += 0.3

        # Size penalty: prefer focused chunks over grab-bags (very gentle).
        if self.size_bytes > 5000:
            score *= 0.9
        return score


@dataclass
class ReferenceIndex:
    chunks: list[ChunkRef] = field(default_factory=list)
    root: Path = field(default_factory=Path)

    @classmethod
    def load(cls, domain_root: Path) -> ReferenceIndex:
        """Load `<domain>/context/reference_index.yaml` if present."""
        idx_path = domain_root / "context" / "reference_index.yaml"
        if not idx_path.exists():
            logger.info("No reference_index.yaml at %s — Tier 2 retrieval disabled", idx_path)
            return cls(chunks=[], root=domain_root)
        try:
            # Hardened YAML load — defends against Billion-Laughs / oversize
            # payloads in reference_index.yaml (generated content can be
            # adversarial if a source repo is hostile).
            from harness.core.yaml_safe import safe_load as _safe_load
            data = _safe_load(
                idx_path.read_text(encoding="utf-8"),
                source=str(idx_path),
            ) or {}
        except Exception as exc:
            logger.warning("Failed to parse reference_index.yaml: %s", exc)
            return cls(chunks=[], root=domain_root)

        chunks: list[ChunkRef] = []
        for entry in data.get("chunks", []) or []:
            if not isinstance(entry, dict):
                continue
            chunk_id = str(entry.get("id", "")).strip()
            path = str(entry.get("path", "")).strip()
            if not chunk_id or not path:
                continue
            kws = entry.get("keywords") or []
            if isinstance(kws, str):
                kws = [k.strip() for k in kws.split(",") if k.strip()]
            chunks.append(ChunkRef(
                id=chunk_id,
                topic=str(entry.get("topic", "")),
                summary=str(entry.get("summary", "")),
                path=path,
                keywords=tuple(str(k) for k in kws),
                size_bytes=int(entry.get("size_bytes") or 0),
            ))
        return cls(chunks=chunks, root=domain_root)

    def keyword_doc_frequency(self) -> dict[str, int]:
        """How many chunks declare each keyword (for inverse-rarity scoring)."""
        freq: dict[str, int] = {}
        for ch in self.chunks:
            for kw in ch.keywords:
                freq[kw.lower()] = freq.get(kw.lower(), 0) + 1
        return freq

    def resolve_chunk_body(self, chunk: ChunkRef) -> str:
        """Read a chunk's markdown body from disk."""
        p = self.root / "context" / chunk.path if not Path(chunk.path).is_absolute() else Path(chunk.path)
        # Handle both `reference/chunks/x.md` and `chunks/x.md` shapes.
        if not p.exists():
            alt = self.root / chunk.path
            if alt.exists():
                p = alt
        try:
            return p.read_text(encoding="utf-8")
        except Exception as exc:
            return f"<error reading chunk {chunk.id}: {exc}>"


@dataclass
class RetrievalResult:
    chunks: list[tuple[ChunkRef, float]]
    query_topic: str
    query_keywords: tuple[str, ...]
    total_candidates: int

    def format_for_llm(self, retriever: ContextRetriever) -> str:
        """Render top chunks as markdown for injection into an agent turn."""
        if not self.chunks:
            return (
                f"[context_retrieve] No reference chunks matched "
                f"topic={self.query_topic!r} keywords={list(self.query_keywords)}.\n"
                "Tier 3 origin_fetch may have what you need; otherwise proceed from standards.md."
            )
        parts = [
            f"[context_retrieve] {len(self.chunks)} chunk(s) matched topic="
            f"{self.query_topic!r} keywords={list(self.query_keywords)}:\n"
        ]
        for chunk, score in self.chunks:
            body = retriever.index.resolve_chunk_body(chunk)
            parts.append(f"### Chunk: `{chunk.id}` (topic: {chunk.topic}, score: {score:.2f})\n\n{body}\n")
        return "\n---\n".join(parts)


class ContextRetriever:
    """Keyword-indexed chunk retrieval for domain agents."""

    def __init__(self, index: ReferenceIndex) -> None:
        self.index = index
        self._doc_freq_cache: dict[str, int] | None = None

    @classmethod
    def for_domain(cls, domain_root: Path | str) -> ContextRetriever:
        return cls(ReferenceIndex.load(Path(domain_root)))

    def _doc_freq(self) -> dict[str, int]:
        if self._doc_freq_cache is None:
            self._doc_freq_cache = self.index.keyword_doc_frequency()
        return self._doc_freq_cache

    def search(
        self,
        topic: str = "",
        keywords: list[str] | tuple[str, ...] | None = None,
        top_k: int = 3,
        min_score: float = 0.5,
    ) -> RetrievalResult:
        """Rank chunks against (topic, keywords) and return top_k above min_score."""
        kws: tuple[str, ...] = tuple(str(k).strip() for k in (keywords or ()) if str(k).strip())
        df = self._doc_freq()
        scored: list[tuple[ChunkRef, float]] = []
        for chunk in self.index.chunks:
            s = chunk.score_for(topic or "", kws, df)
            if s >= min_score:
                scored.append((chunk, s))
        scored.sort(key=lambda t: (-t[1], t[0].id))
        return RetrievalResult(
            chunks=scored[:top_k],
            query_topic=topic,
            query_keywords=kws,
            total_candidates=len(self.index.chunks),
        )

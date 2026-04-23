"""Best-practice library loader.

Reads curated industry YAMLs under `config/best_practices/<industry>.yaml`
and exposes them as typed `BestPracticeLibrary` objects for agents that
generate or consume the Tier 1.5 overlay:

  * `BestPracticeResearchAgent` — uses the library as the knowledge_map
    source when genesis runs with a prompt but no repos.
  * `BestPracticeAdvisorAgent` — diffs the library against the
    domain's generated standards.md to produce `best_practices.md`.

The library files themselves are plain YAML so platform-team authors
can extend them without touching code. See
`config/best_practices/_schema.yaml` for the authoring contract.

Zero LLM calls, deterministic parsing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


_DEFAULT_LIB_DIR = Path(__file__).resolve().parent.parent.parent / "config" / "best_practices"


@dataclass
class Principle:
    """One best-practice entry from an industry library."""

    id: str
    title: str
    severity: str              # "must" | "should" | "nice"
    why: str
    must_have: list[str] = field(default_factory=list)
    anti_patterns: list[str] = field(default_factory=list)
    related_chunks: list[str] = field(default_factory=list)
    source_urls: list[str] = field(default_factory=list)


@dataclass
class BestPracticeLibrary:
    """Parsed contents of a `config/best_practices/<industry>.yaml`."""

    industry: str
    description: str = ""
    principles: list[Principle] = field(default_factory=list)
    anti_patterns: list[str] = field(default_factory=list)
    canonical_sources: list[str] = field(default_factory=list)
    version: str = "1.0"

    def by_severity(self, severity: str) -> list[Principle]:
        """Return only principles with the given severity."""
        return [p for p in self.principles if p.severity.lower() == severity.lower()]

    def by_id(self, principle_id: str) -> Principle | None:
        """Look up a single principle by its stable id."""
        for p in self.principles:
            if p.id == principle_id:
                return p
        return None

    def must_ids(self) -> set[str]:
        return {p.id for p in self.principles if p.severity.lower() == "must"}

    def principle_ids(self) -> set[str]:
        return {p.id for p in self.principles}


_VALID_SEVERITIES = frozenset({"must", "should", "nice"})


def _parse_principle(raw: dict[str, Any]) -> Principle | None:
    """Parse one principle dict from YAML. Returns None for unusable entries."""
    if not isinstance(raw, dict):
        return None
    pid = str(raw.get("id", "")).strip()
    title = str(raw.get("title", "")).strip()
    if not pid or not title:
        return None
    severity = str(raw.get("severity", "should")).strip().lower()
    if severity not in _VALID_SEVERITIES:
        logger.warning(
            "best_practice principle %r has unknown severity %r — treating as 'should'",
            pid, severity,
        )
        severity = "should"
    return Principle(
        id=pid,
        title=title,
        severity=severity,
        why=str(raw.get("why", "")).strip(),
        must_have=[str(x) for x in raw.get("must_have") or []],
        anti_patterns=[str(x) for x in raw.get("anti_patterns") or []],
        related_chunks=[str(x) for x in raw.get("related_chunks") or []],
        source_urls=[str(x) for x in raw.get("source_urls") or []],
    )


def load_library(industry: str, *, library_dir: Path | None = None) -> BestPracticeLibrary | None:
    """Load `config/best_practices/<industry>.yaml` if it exists.

    Returns None when:
      * the industry name is empty
      * no matching file exists
      * the file fails to parse

    The schema-doc file `_schema.yaml` is explicitly skipped (leading
    underscore is the convention for authoring docs).
    """
    if not industry:
        return None
    normalized = industry.strip().lower().replace(" ", "_")
    if not normalized or normalized.startswith("_"):
        return None
    base = Path(library_dir) if library_dir else _DEFAULT_LIB_DIR
    candidate = base / f"{normalized}.yaml"
    if not candidate.exists():
        return None
    try:
        raw = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        logger.warning("best_practices library %s failed to parse: %s", candidate, exc)
        return None
    if not isinstance(raw, dict):
        return None

    principles: list[Principle] = []
    for p_raw in raw.get("principles") or []:
        principle = _parse_principle(p_raw)
        if principle is not None:
            principles.append(principle)

    return BestPracticeLibrary(
        industry=str(raw.get("industry") or normalized),
        description=str(raw.get("description") or "").strip(),
        principles=principles,
        anti_patterns=[str(x) for x in raw.get("anti_patterns") or []],
        canonical_sources=[str(x) for x in raw.get("canonical_sources") or []],
        version=str(raw.get("version") or "1.0"),
    )


def list_available_industries(*, library_dir: Path | None = None) -> list[str]:
    """Return industry names for which a library file exists (sorted).

    Skips `_schema.yaml` and any other leading-underscore file.
    """
    base = Path(library_dir) if library_dir else _DEFAULT_LIB_DIR
    if not base.is_dir():
        return []
    out: list[str] = []
    for p in base.iterdir():
        if not p.is_file() or p.suffix not in {".yaml", ".yml"}:
            continue
        if p.stem.startswith("_"):
            continue
        out.append(p.stem)
    return sorted(out)

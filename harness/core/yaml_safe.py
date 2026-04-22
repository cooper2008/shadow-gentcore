"""Hardened YAML loader — protects against resource exhaustion attacks.

`yaml.safe_load` prevents arbitrary code execution (no `!!python/object`),
but it does NOT prevent:
  * The "Billion Laughs" attack via recursive aliases (`&a`, `*a`).
  * Memory exhaustion via very large documents.
  * Excessive depth / blow-up via deeply nested structures.

This module provides `safe_load(content, *, max_size_bytes, max_aliases,
max_depth)` that rejects these before they reach the parser.

When a generated tool pack YAML, reference_index.yaml, or agent manifest is
about to be parsed, use `safe_load` here instead of `yaml.safe_load`. For
trusted config files (`config/rules.yaml`, framework-internal YAML), direct
`yaml.safe_load` is fine — the attack surface is LLM-generated content
and inputs from remote sources.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import yaml

logger = logging.getLogger(__name__)


# Default limits.
#
# size: 2 MB is far above any legitimate pack / index / manifest
#       but far below a typical memory-exhaustion payload.
# aliases: 50 is generous; legitimate YAML rarely uses aliases at all,
#          and Billion Laughs style attacks need thousands.
# depth: 32 is 2× the deepest nesting seen in framework YAML.

DEFAULT_MAX_SIZE_BYTES = 2 * 1024 * 1024
DEFAULT_MAX_ALIASES = 50
DEFAULT_MAX_DEPTH = 32


class YamlLoadError(ValueError):
    """Raised when a YAML document fails the safety preflight or parses into
    a structure that exceeds limits."""


# Rough alias-count check: a YAML bomb chains `*foo` anchors hundreds of
# times. Legitimate YAML almost never aliases, so "many aliases" is a
# strong heuristic for attack. This is a substring count, not a parser —
# intentionally cheap, runs before any allocation.
_ALIAS_PATTERN = re.compile(r"(?:(?<=[\s\[\{,:])|^)\*[A-Za-z_][A-Za-z0-9_\-]*")


def _count_aliases(content: str) -> int:
    return len(_ALIAS_PATTERN.findall(content))


def _structural_depth(value: Any, limit: int) -> int:
    """Return max depth of a parsed structure, short-circuiting at `limit`."""
    stack: list[tuple[Any, int]] = [(value, 1)]
    seen: set[int] = set()
    max_d = 0
    while stack:
        cur, depth = stack.pop()
        if depth > limit:
            return depth
        max_d = max(max_d, depth)
        # Cycle guard
        cid = id(cur)
        if cid in seen:
            continue
        seen.add(cid)
        if isinstance(cur, dict):
            for v in cur.values():
                if isinstance(v, (dict, list)):
                    stack.append((v, depth + 1))
        elif isinstance(cur, list):
            for v in cur:
                if isinstance(v, (dict, list)):
                    stack.append((v, depth + 1))
    return max_d


def safe_load(
    content: str | bytes,
    *,
    max_size_bytes: int = DEFAULT_MAX_SIZE_BYTES,
    max_aliases: int = DEFAULT_MAX_ALIASES,
    max_depth: int = DEFAULT_MAX_DEPTH,
    source: str | None = None,
) -> Any:
    """Parse `content` as YAML after running three cheap safety checks.

    Args:
        content: raw YAML string or bytes.
        max_size_bytes: hard size cap. Raises before parsing.
        max_aliases: reject if raw text contains more `*foo` aliases than this.
        max_depth: reject if parsed structure nests deeper than this.
        source: optional source label (filename / pack id) for error context.

    Returns:
        Parsed Python structure (dict / list / primitives) or None for empty.

    Raises:
        YamlLoadError on any safety-check violation or underlying parse error.
    """
    if isinstance(content, bytes):
        try:
            content = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise YamlLoadError(
                f"YAML content is not valid UTF-8 ({source or 'unknown'}): {exc}"
            ) from exc

    label = f" [{source}]" if source else ""

    if len(content) > max_size_bytes:
        raise YamlLoadError(
            f"YAML document{label} exceeds size cap "
            f"({len(content)} > {max_size_bytes} bytes). Refusing to parse."
        )

    # Cheap pre-parse alias count — a Billion Laughs bomb needs thousands of
    # aliases. Legitimate framework YAML rarely uses aliases at all.
    alias_count = _count_aliases(content)
    if alias_count > max_aliases:
        raise YamlLoadError(
            f"YAML document{label} contains {alias_count} aliases "
            f"(> cap {max_aliases}). Refusing to parse to prevent "
            f"Billion-Laughs-style resource exhaustion."
        )

    try:
        parsed = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise YamlLoadError(f"YAML parse failed{label}: {exc}") from exc

    if isinstance(parsed, (dict, list)):
        depth = _structural_depth(parsed, max_depth)
        if depth > max_depth:
            raise YamlLoadError(
                f"YAML document{label} nests {depth} levels "
                f"(> cap {max_depth}). Refusing — likely malformed or adversarial."
            )

    return parsed


def safe_load_file(
    path: Any,  # Path — typed as Any to avoid circular imports
    **kwargs: Any,
) -> Any:
    """Convenience wrapper: safe_load from a Path object."""
    from pathlib import Path as _Path
    p = _Path(path)
    content = p.read_text(encoding="utf-8")
    kwargs.setdefault("source", str(p))
    return safe_load(content, **kwargs)

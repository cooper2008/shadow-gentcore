"""Unified expression evaluator (H2).

Single grammar shared by:
  - CompositionEngine._evaluate_condition (gate + feedback-loop conditions)
  - OutputValidator._eval_check          (grading criteria)

Replaces two divergent ad-hoc parsers. Behaviour is the union of both, with
backward-compatible legacy aliases preserved verbatim so existing workflows
and grading_criteria.yaml files keep working without edits.

Grammar (recursive descent):
    expr      := or_expr
    or_expr   := and_expr ("or" and_expr)*
    and_expr  := not_expr ("and" not_expr)*
    not_expr  := "not" not_expr | cmp_expr
    cmp_expr  := atom (cmp_tail)?
    cmp_tail  := cmp_op atom
               | "is" "not"? ("None" | "null" | "empty")
               | "contains" atom
               | "in" atom
    cmp_op    := "==" | "!=" | ">=" | "<=" | ">" | "<"
    atom      := "(" expr ")"
               | "len" "(" dotpath ")"
               | literal
               | dotpath
    literal   := number | quoted_string | "true" | "false" | "none" | "null"
    dotpath   := word ("." word)*

Legacy aliases (handled before the parser, must stay byte-identical):
    true / always / always_pass         -> True
    false / always_fail                 -> False
    has_output                          -> bool(ctx.output | ctx.content)
    score >= N                          -> ctx._validation.score >= N
    <field>_exists                      -> resolved field is not None
    <dotpath> is not empty              -> bool(resolved value)
    bare dotpath without operator       -> bool(resolved value)

Failure modes:
    - Unparseable expressions log a warning and return False (fail-closed —
      gates won't pass on garbage). Callers should treat the warning as a
      bug-report signal.
    - Unknown identifiers in a dotpath resolve to None and propagate that
      None into the comparator (numeric ops with None → False; ==/!= treat
      None as the literal string "none").
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


# ── Legacy alias fast-paths ─────────────────────────────────────────────────


_SCORE_RE = re.compile(r"^score\s*>=\s*([0-9]*\.?[0-9]+)$", re.IGNORECASE)
_BARE_FIELD_RE = re.compile(r"^[A-Za-z_][\w]*$")
_NOT_EMPTY_RE = re.compile(r"^([A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)*)\s+is\s+not\s+empty$", re.IGNORECASE)


def _legacy_alias(expr: str, ctx: dict[str, Any]) -> bool | None:
    """Match legacy patterns. Returns the boolean result, or None if no alias matched.

    Preserves byte-identical behaviour with the pre-H2 implementations of
    CompositionEngine._evaluate_condition and OutputValidator._eval_check.
    """
    s = expr.strip()
    lower = s.lower()

    if lower in ("true", "always", "always_pass"):
        return True
    if lower in ("false", "always_fail"):
        return False
    if lower == "has_output":
        out = ctx.get("output") or ctx.get("content") or ""
        return bool(out)

    m = _SCORE_RE.match(lower)
    if m:
        threshold = float(m.group(1))
        actual = ctx.get("_validation", {}).get("score", 0) if isinstance(ctx.get("_validation"), dict) else 0
        try:
            return float(actual) >= threshold
        except (TypeError, ValueError):
            return False

    # `<field>_exists` shortcut (output_validator legacy)
    if lower.endswith("_exists") and _BARE_FIELD_RE.match(s.replace("_exists", "")):
        field = s[: -len("_exists")]
        return _resolve_field(ctx, field) is not None

    # `<dotpath> is not empty` (composition_engine legacy)
    m = _NOT_EMPTY_RE.match(s)
    if m:
        return bool(_resolve_field(ctx, m.group(1)))

    return None


# ── Field resolution (dot-notation, dict + attr) ────────────────────────────


def _resolve_field(ctx: Any, path: str) -> Any:
    """Walk a dot-notated path through dicts/objects. Returns None on miss."""
    current: Any = ctx
    for part in path.split("."):
        if current is None:
            return None
        if isinstance(current, dict):
            current = current.get(part)
        elif hasattr(current, part):
            current = getattr(current, part)
        else:
            return None
    return current


# ── Tokenizer ───────────────────────────────────────────────────────────────


_TOKEN_RE = re.compile(
    r"""
    \s*(?:
        (?P<paren>[()])
      | (?P<op>==|!=|>=|<=|>|<)
      | (?P<num>-?\d+(?:\.\d+)?)
      | (?P<str>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')
      | (?P<word>[A-Za-z_][\w.]*)
      | (?P<comma>,)
    )
    """,
    re.VERBOSE,
)


def _tokenize(expr: str) -> list[tuple[str, str]]:
    """Lex the expression into (kind, text) tokens. Raises ValueError on garbage."""
    tokens: list[tuple[str, str]] = []
    pos = 0
    while pos < len(expr):
        m = _TOKEN_RE.match(expr, pos)
        if not m or m.end() == pos:
            raise ValueError(f"unexpected character at position {pos}: {expr[pos:pos+20]!r}")
        kind = m.lastgroup or ""
        text = m.group(kind)
        tokens.append((kind, text))
        pos = m.end()
    tokens.append(("end", ""))
    return tokens


# ── Parser + interpreter (single pass) ──────────────────────────────────────


class _Parser:
    """Recursive descent parser that evaluates as it parses (no separate AST)."""

    KEYWORDS = {"and", "or", "not", "is", "in", "contains", "len", "true", "false", "none", "null", "empty"}

    def __init__(self, tokens: list[tuple[str, str]], ctx: dict[str, Any]) -> None:
        self._tokens = tokens
        self._pos = 0
        self._ctx = ctx

    # -- token helpers ---------------------------------------------------
    def _peek(self) -> tuple[str, str]:
        return self._tokens[self._pos]

    def _advance(self) -> tuple[str, str]:
        tok = self._tokens[self._pos]
        self._pos += 1
        return tok

    def _accept_word(self, *words: str) -> str | None:
        kind, text = self._peek()
        if kind == "word" and text.lower() in words:
            self._advance()
            return text.lower()
        return None

    # -- grammar ---------------------------------------------------------
    def parse(self) -> Any:
        value = self._parse_or()
        kind, _ = self._peek()
        if kind != "end":
            raise ValueError(f"trailing tokens after expression at position {self._pos}")
        return value

    def _parse_or(self) -> Any:
        left = self._parse_and()
        while self._accept_word("or"):
            right = self._parse_and()
            left = bool(left) or bool(right)
        return left

    def _parse_and(self) -> Any:
        left = self._parse_not()
        while self._accept_word("and"):
            right = self._parse_not()
            left = bool(left) and bool(right)
        return left

    def _parse_not(self) -> Any:
        if self._accept_word("not"):
            return not bool(self._parse_not())
        return self._parse_cmp()

    def _parse_cmp(self) -> Any:
        left = self._parse_atom()
        kind, text = self._peek()

        # cmp_op atom — RHS parses as a LITERAL VALUE, not a field lookup.
        # This matches legacy behaviour: `status == success` compares the
        # value of `status` to the literal string "success" (not to a field
        # named `success`).
        if kind == "op":
            self._advance()
            right = self._parse_value_atom()
            return _compare(left, text, right)

        # is [not] (None | null | empty)
        if self._accept_word("is"):
            negate = bool(self._accept_word("not"))
            tail = self._accept_word("none", "null", "empty")
            if tail is None:
                raise ValueError("expected 'None', 'null', or 'empty' after 'is [not]'")
            if tail == "empty":
                empty = not bool(left)
                return (not empty) if negate else empty
            is_none = left is None
            return (not is_none) if negate else is_none

        # contains atom (RHS literal — same rule as comparators)
        if self._accept_word("contains"):
            right = self._parse_value_atom()
            return _contains(left, right)

        # in atom (RHS is the haystack — keep as field lookup so `x in tags` works)
        if self._accept_word("in"):
            right = self._parse_atom()
            return _contains(right, left)

        # No comparator → truthy check on bare value
        return bool(left) if left is not None else False

    def _parse_atom(self) -> Any:
        kind, text = self._advance()
        if kind == "paren" and text == "(":
            value = self._parse_or()
            close_kind, close_text = self._advance()
            if close_kind != "paren" or close_text != ")":
                raise ValueError("expected ')'")
            return value
        if kind == "num":
            return float(text) if "." in text else int(text)
        if kind == "str":
            return text[1:-1]
        if kind == "word":
            lower = text.lower()
            if lower in ("true",):
                return True
            if lower in ("false",):
                return False
            if lower in ("none", "null"):
                return None
            if lower == "len":
                # len ( dotpath )
                paren_kind, paren_text = self._advance()
                if paren_kind != "paren" or paren_text != "(":
                    raise ValueError("expected '(' after 'len'")
                inner_kind, inner_text = self._advance()
                if inner_kind != "word":
                    raise ValueError("'len' expects a dotpath argument")
                close_kind, close_text = self._advance()
                if close_kind != "paren" or close_text != ")":
                    raise ValueError("expected ')' after len argument")
                value = _resolve_field(self._ctx, inner_text)
                try:
                    return len(value)  # type: ignore[arg-type]
                except TypeError:
                    return 0
            if lower == "has_output":
                # Recognised inside expressions too (e.g. `not has_output`),
                # not just the legacy fast-path.
                return bool(self._ctx.get("output") or self._ctx.get("content") or "")
            return _resolve_field(self._ctx, text)
        raise ValueError(f"unexpected token: {kind}={text!r}")

    def _parse_value_atom(self) -> Any:
        """Like _parse_atom, but bare unquoted words are treated as string literals.

        This is the RHS-of-comparator semantics: in `status == success`, the
        token `success` is the literal string "success", not a field lookup.
        Dotted words (e.g. `a.b.c`) are still treated as literals here, which
        matches legacy regex-based RHS capture in both engines.
        """
        kind, text = self._advance()
        if kind == "paren" and text == "(":
            value = self._parse_or()
            close_kind, close_text = self._advance()
            if close_kind != "paren" or close_text != ")":
                raise ValueError("expected ')'")
            return value
        if kind == "num":
            return float(text) if "." in text else int(text)
        if kind == "str":
            return text[1:-1]
        if kind == "word":
            lower = text.lower()
            if lower == "true":
                return True
            if lower == "false":
                return False
            if lower in ("none", "null"):
                return None
            # Any other bare word (or dotted word like `a.b.c`) → literal string
            return text
        raise ValueError(f"unexpected token in RHS: {kind}={text!r}")


# ── Comparators ─────────────────────────────────────────────────────────────


def _compare(left: Any, op: str, right: Any) -> bool:
    """Compare with output_validator's numeric-first-then-string semantics.

    - Numbers compare numerically (both sides coerce to float).
    - If numeric coercion fails, fall back to case-insensitive string compare.
    - None on either side: ordering ops (<, <=, >, >=) fail closed (False).
      Equality (==/!=) treats None as the literal string "none" — preserves
      composition_engine's legacy `<dotpath> == none` behaviour.
    """
    if left is None or right is None:
        if op not in ("==", "!="):
            return False  # ordering against None is undefined → fail-closed
        ls = "none" if left is None else str(left).lower()
        rs = "none" if right is None else str(right).lower()
        return (ls == rs) if op == "==" else (ls != rs)

    _NUMERIC_OPS = {
        "==": lambda a, b: a == b,
        "!=": lambda a, b: a != b,
        ">=": lambda a, b: a >= b,
        "<=": lambda a, b: a <= b,
        ">": lambda a, b: a > b,
        "<": lambda a, b: a < b,
    }

    try:
        ln = float(left)  # type: ignore[arg-type]
        rn = float(right)  # type: ignore[arg-type]
        fn = _NUMERIC_OPS.get(op)
        if fn is not None:
            return fn(ln, rn)
    except (TypeError, ValueError):
        pass

    ls = str(left).lower()
    rs = str(right).lower()
    fn = _NUMERIC_OPS.get(op)
    if fn is not None:
        return fn(ls, rs)
    return False


def _contains(haystack: Any, needle: Any) -> bool:
    if haystack is None:
        return False
    if isinstance(haystack, str):
        return str(needle).lower() in haystack.lower()
    try:
        return needle in haystack
    except TypeError:
        return False


# ── Public entry point ─────────────────────────────────────────────────────


def evaluate(expr: str, ctx: dict[str, Any]) -> bool:
    """Evaluate an expression against a context dict and return a boolean.

    Args:
        expr: expression source. May be a legacy alias or a full grammar expression.
        ctx:  context bag — gate evaluators pass step result; output validators
              pass the agent output dict. Identifiers in the expression resolve
              against this dict.

    Returns:
        Boolean. Unparseable expressions log a warning and return False.
    """
    if not isinstance(expr, str) or not expr.strip():
        return False

    legacy = _legacy_alias(expr, ctx)
    if legacy is not None:
        return legacy

    try:
        tokens = _tokenize(expr)
    except ValueError as exc:
        logger.warning("Failed to tokenize expression %r: %s — Unrecognized expression, failing closed", expr, exc)
        return False

    parser = _Parser(tokens, ctx)
    try:
        return bool(parser.parse())
    except ValueError as exc:
        logger.warning("Failed to evaluate expression %r: %s — Unrecognized expression, failing closed", expr, exc)
        return False
    except Exception as exc:  # noqa: BLE001 — defensive: never crash a workflow
        logger.warning("Unexpected error evaluating %r: %s — Unrecognized expression, failing closed", expr, exc)
        return False

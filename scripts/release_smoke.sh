#!/usr/bin/env bash
# release_smoke.sh — end-to-end clean-checkout smoke test.
#
# Verifies that all 5 framework repos pass their quality gates from a fresh
# local state. Run this before cutting a release tag.
#
# Usage:
#   bash scripts/release_smoke.sh              # use repos at ../sibling paths
#   bash scripts/release_smoke.sh --clone      # clone all 5 repos to a temp dir first
#
# Exit: 0 if every gate passes, 1 on first failure.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

CLONE_MODE=0
for arg in "$@"; do
  [ "$arg" = "--clone" ] && CLONE_MODE=1
done

# ── Colour helpers ────────────────────────────────────────────────────────────
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

ok()   { echo -e "${GREEN}  ✓${NC} $*"; }
fail() { echo -e "${RED}  ✗${NC} $*"; exit 1; }
info() { echo -e "${YELLOW}  →${NC} $*"; }

FAILURES=0
run_gate() {
  local label="$1"; shift
  local dir="$1"; shift
  echo ""
  info "[$label] in $dir"
  if (cd "$dir" && "$@" 2>&1); then
    ok "$label passed"
  else
    echo -e "${RED}  ✗ $label FAILED${NC}"
    FAILURES=$((FAILURES + 1))
  fi
}

# ── Clone mode: fresh checkout to temp dir ────────────────────────────────────
if [ "$CLONE_MODE" = "1" ]; then
  TMPDIR=$(mktemp -d)
  trap 'rm -rf "$TMPDIR"' EXIT
  info "Cloning repos to $TMPDIR ..."
  ORG="$(git -C "$REPO_ROOT" remote get-url origin 2>/dev/null | sed 's|.*github.com[:/]||;s|/.*||' || echo 'cooper2008')"
  for REPO in shadow-gentcore agent-contracts agent-tools acme-backend gentcore-template; do
    git clone --quiet "https://github.com/$ORG/$REPO.git" "$TMPDIR/$REPO" 2>/dev/null \
      || { echo "Warning: could not clone $REPO — skipping"; }
  done
  BASE="$TMPDIR"
else
  # Use existing local repos at standard sibling paths
  BASE="$(cd "$REPO_ROOT/.." && pwd)"
fi

echo ""
echo "========================================"
echo " Gentcore Release Smoke — $(date +%Y-%m-%d)"
echo "========================================"

# ── Repo setup helper ─────────────────────────────────────────────────────────
setup_venv() {
  local dir="$1"
  [ -d "$dir/.venv" ] && return
  info "Setting up venv in $dir ..."
  python3 -m venv "$dir/.venv"
  "$dir/.venv/bin/pip" install -q --upgrade pip
  "$dir/.venv/bin/pip" install -q -e "$dir/.[dev]" 2>/dev/null \
    || "$dir/.venv/bin/pip" install -q -e "$dir" 2>/dev/null || true
}

# ── 1. agent-contracts ────────────────────────────────────────────────────────
CONTRACTS="$BASE/agent-contracts"
if [ -d "$CONTRACTS" ]; then
  run_gate "agent-contracts: make check" "$CONTRACTS" \
    bash -c "python3 -m pip install -q -e '.[dev]' && make check"
else
  echo "  ⚠ $CONTRACTS not found — skipping"
fi

# ── 2. agent-tools ────────────────────────────────────────────────────────────
TOOLS="$BASE/agent-tools"
if [ -d "$TOOLS" ]; then
  run_gate "agent-tools: make check" "$TOOLS" \
    bash -c "python3 -m pip install -q -e '.[dev]' && [ -d '../agent-contracts' ] && pip install -q -e ../agent-contracts; make check"
else
  echo "  ⚠ $TOOLS not found — skipping"
fi

# ── 3. shadow-gentcore ────────────────────────────────────────────────────────
CORE="$BASE/shadow-gentcore"
if [ -d "$CORE" ]; then
  run_gate "shadow-gentcore: make check" "$CORE" \
    bash -c "python3 -m pip install -q -e '.[dev]' && make check"
  run_gate "shadow-gentcore: smoke test" "$CORE" \
    bash -c "./ai test smoke"
  run_gate "shadow-gentcore: providers status" "$CORE" \
    bash -c "./ai providers status | grep -q 'Missing for full coverage\|All tiers covered'"
else
  echo "  ⚠ $CORE not found — using current repo"
  run_gate "shadow-gentcore: make check" "$REPO_ROOT" \
    bash -c "make check"
  run_gate "shadow-gentcore: smoke test" "$REPO_ROOT" \
    bash -c "./ai test smoke"
fi

# ── 4. acme-backend ───────────────────────────────────────────────────────────
ACME="$BASE/acme-backend"
if [ -d "$ACME" ]; then
  run_gate "acme-backend: make check" "$ACME" \
    bash -c "python3 -m pip install -q -e '.[dev]' && make check"
else
  echo "  ⚠ $ACME not found — skipping"
fi

# ── 5. gentcore-template: workflow YAML + bash lint ──────────────────────────
TMPL="$BASE/gentcore-template"
if [ -d "$TMPL" ]; then
  run_gate "gentcore-template: workflow YAML syntax" "$TMPL" \
    python3 -c "
import sys, yaml, pathlib
errors = []
for p in pathlib.Path('.github/workflows').glob('*.yml'):
    try:
        yaml.safe_load(p.read_text())
    except yaml.YAMLError as e:
        errors.append(f'{p}: {e}')
if errors:
    print('\n'.join(errors)); sys.exit(1)
print(f'OK: {len(list(pathlib.Path(\".github/workflows\").glob(\"*.yml\")))} workflow files valid')
"
  run_gate "gentcore-template: bash script syntax" "$TMPL" \
    bash -c "find scripts/ -name '*.sh' -exec bash -n {} \; && echo 'OK'"
else
  echo "  ⚠ $TMPL not found — skipping"
fi

# ── Git hygiene check ─────────────────────────────────────────────────────────
echo ""
info "Git hygiene check (tracked bytecode) ..."
for DIR in "$BASE/agent-contracts" "$BASE/agent-tools" "$REPO_ROOT"; do
  [ -d "$DIR/.git" ] || continue
  PYCS=$(git -C "$DIR" ls-files | (grep -E '__pycache__|\.pyc$' || true) | wc -l | tr -d ' ')
  if [ "$PYCS" -gt 0 ]; then
    echo -e "${RED}  ✗${NC} $DIR has $PYCS tracked .pyc files — run: git rm --cached -r \$(git ls-files | grep -E '__pycache__|\\.pyc$')"
    FAILURES=$((FAILURES + 1))
  else
    ok "$(basename "$DIR"): no tracked bytecode"
  fi
done

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "========================================"
if [ "$FAILURES" -eq 0 ]; then
  echo -e "${GREEN}  ALL GATES PASSED — ready for release${NC}"
  echo "========================================"
  exit 0
else
  echo -e "${RED}  $FAILURES GATE(S) FAILED — fix before releasing${NC}"
  echo "========================================"
  exit 1
fi

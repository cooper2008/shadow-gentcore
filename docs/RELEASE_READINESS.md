# Release Readiness

Current maturity: **developer preview → production-ready (v1.0)**.

This document tracks the Go/No-Go checklist from the 2026-Q2 production-readiness audit
(`docs/FRAMEWORK_AUDIT_2026Q2.md`). Update it when gate status changes.

---

## Go/No-Go Checklist

| # | Category | Status | Notes |
|---|----------|--------|-------|
| C1 | Clean quality gates (`make check` exits 0) | ✅ RESOLVED | ruff 0 errors, mypy 0 errors, 2282 tests pass |
| C2 | Default-on rule enforcement | ✅ RESOLVED | `GENTCORE_UNSAFE_DISABLE_RULES` opt-out; rules on by default |
| C3 | MCP remote transport `success=False` | ✅ RESOLVED | error path sets `success: false` |
| C4 | Template `git add -A` removed | ✅ RESOLVED | explicit paths only in agent-task.yml |
| C5 | Secret scan before write-back | ✅ RESOLVED | gitleaks gate in gentcore-template agent-task.yml |

---

## Quality Gate Details

### shadow-gentcore
- `ruff check harness/ agents/ workflows/` — **0 errors**
- `mypy --cache-dir=/dev/null harness/` — **0 errors (103 source files)**
- `pytest harness/tests/ -q` — **2282 passed, 6 skipped**
- `harness/tests/` excluded from mypy strict mode (see `mypy.ini`) — tracked as T2.1 burndown
  in the subsequent milestone

### agent-contracts
- `make check` — **0 errors, 66 tests pass**

### agent-tools
- `make check` — **0 errors, 36 tests pass**

### acme-backend
- `make check` — **0 errors, 4 tests pass**
- Marked DEMO ONLY — placeholder auth, no transaction guards; not for production

### gentcore-template
- CI validates workflow YAML syntax and bash script syntax
- Secret scan (gitleaks) blocks commit if staged output contains secrets

---

## Known Limitations

1. **`harness/tests/` mypy exclusion** — ~750 test annotation errors deferred to T2.1 burndown.
   Core engine (`harness/core/`, `harness/providers/`, `harness/server/`, `harness/cli/`) is 0-error.

2. **acme-backend auth** — uses `hashed:{password}` placeholder; needs bcrypt before any public deployment.

3. **Test isolation** — server tests (`test_server_auth.py`, `test_approval_gate.py`) pass in isolation
   and in the standard `pytest harness/tests/` run, but flake under certain parallel execution orders.
   Tracked separately; not a release blocker.

---

## How to Run the Full Gate

```bash
# shadow-gentcore
make check                       # ruff + mypy + pytest

# sibling repos
cd ../agent-contracts && make check
cd ../agent-tools && make check
cd ../acme-backend && make check

# end-to-end smoke (no API key required)
./ai test smoke

# multi-provider detection
./ai providers detect
./ai providers status
```

All five must exit `0` for a v1.0 release claim.

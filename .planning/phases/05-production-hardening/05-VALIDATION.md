---
phase: 5
slug: production-hardening
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-18
---

# Phase 5 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest |
| **Config file** | `documentologist-miran-service--neo4j/langgraph-agent/pytest.ini` |
| **Quick run command** | `cd documentologist-miran-service--neo4j/langgraph-agent && python -m pytest tests/integration/ -x -q` |
| **Full suite command** | `cd documentologist-miran-service--neo4j/langgraph-agent && python -m pytest tests/ -x -q` |
| **Estimated runtime** | ~20 seconds (mocked, no live deps) |

---

## Sampling Rate

- **After every task commit:** Run quick run command
- **After every plan wave:** Run full suite command
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 20 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 05-01-01 | 01 | 1 | integration tests | integration | `pytest tests/integration/ -x -q` | ❌ W0 | ⬜ pending |
| 05-01-02 | 01 | 1 | /health Ollama check | unit | `pytest tests/unit/test_health.py -x -q` | ❌ W0 | ⬜ pending |
| 05-02-01 | 02 | 1 | .env.example complete | static | `python -c "..."` inline check | ✅ | ⬜ pending |
| 05-02-02 | 02 | 1 | Makefile commands | static | `make --dry-run test eval lint` | ✅ | ⬜ pending |
| 05-03-01 | 03 | 2 | docker-compose.prod.yml | static | `docker-compose -f docker-compose.prod.yml config` | ❌ W0 | ⬜ pending |
| 05-03-02 | 03 | 2 | eval metrics documented | static | file exists check | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/integration/__init__.py` + `tests/integration/conftest.py` — fixtures with graph.invoke mock
- [ ] `tests/integration/test_search.py` — search agent happy path stub
- [ ] `tests/integration/test_analyze.py` — analyze qa/compare/verify/generate stubs
- [ ] `tests/integration/test_ingest.py` — ingest happy path stub
- [ ] `tests/unit/test_health.py` — health endpoint unit test stub

*If framework is already installed: only stub files needed.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| `docker-compose -f docker-compose.prod.yml up` works | UAT | Requires Docker + all deps | Run on target machine with populated .env |
| Eval suite v1.0 metrics | UAT | Requires live stack | Run `make eval` with full stack up, record output |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 20s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending

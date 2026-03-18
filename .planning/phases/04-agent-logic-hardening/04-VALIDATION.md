---
phase: 4
slug: agent-logic-hardening
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-18
---

# Phase 4 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest |
| **Config file** | `documentologist-miran-service--neo4j/langgraph-agent/pytest.ini` (or pyproject.toml) |
| **Quick run command** | `cd documentologist-miran-service--neo4j/langgraph-agent && python -m pytest tests/unit/ -x -q` |
| **Full suite command** | `cd documentologist-miran-service--neo4j/langgraph-agent && python -m pytest tests/ -x -q` |
| **Estimated runtime** | ~30 seconds |

---

## Sampling Rate

- **After every task commit:** Run quick run command
- **After every plan wave:** Run full suite command
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 04-01-01 | 01 | 1 | supervisor patterns | unit | `pytest tests/unit/test_supervisor.py -x -q` | ❌ W0 | ⬜ pending |
| 04-01-02 | 01 | 1 | supervisor logging | unit | `pytest tests/unit/test_supervisor.py -x -q` | ❌ W0 | ⬜ pending |
| 04-02-01 | 02 | 1 | compare dedup | unit | `pytest tests/unit/test_analyze_agent.py -x -q` | ❌ W0 | ⬜ pending |
| 04-03-01 | 03 | 2 | hallucination guard | unit | `pytest tests/unit/test_search_agent.py -x -q` | ❌ W0 | ⬜ pending |
| 04-04-01 | 04 | 2 | graceful degradation | unit | `pytest tests/unit/test_graceful_degradation.py -x -q` | ❌ W0 | ⬜ pending |
| 04-04-02 | 04 | 2 | redis non-fatal | unit | `pytest tests/unit/test_memory_agent.py -x -q` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/unit/test_supervisor.py` — stubs for routing pattern + logging assertions
- [ ] `tests/unit/test_analyze_agent.py` — stubs for compare dedup verification
- [ ] `tests/unit/test_search_agent.py` — stubs for hallucination guard
- [ ] `tests/unit/test_graceful_degradation.py` — stubs for Ollama timeout + Redis failure

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Routing accuracy ≥ 90% | Eval suite | Requires live API + Neo4j | Run `python tests/eval/run_eval.py` with stack up |
| Redis failure graceful | UAT | Requires stopping Redis | `docker stop redis` → send query → verify 200 response |
| Ollama timeout graceful | UAT | Requires killing Ollama | Stop Ollama → send query → verify 200 with error message |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending

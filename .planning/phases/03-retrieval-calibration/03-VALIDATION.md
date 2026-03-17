---
phase: 3
slug: retrieval-calibration
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-17
---

# Phase 3 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x |
| **Config file** | documentologist-miran-service--neo4j/langgraph-agent/pytest.ini (or pyproject.toml) |
| **Quick run command** | `python tests/eval/run_eval.py` |
| **Full suite command** | `python tests/eval/run_eval.py` |
| **Estimated runtime** | ~60-120 seconds (requires running API) |

---

## Sampling Rate

- **After every task commit:** Verify file changes via grep/read
- **After every plan wave:** Run `python tests/eval/run_eval.py`
- **Before `/gsd:verify-work`:** Full eval suite must show retrieval_recall_at_5 ≥ 0.80
- **Max feedback latency:** ~120 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 3-01-01 | 01 | 1 | FR-2 | grep | `grep -n "_expand_query" search_agent.py` returns 0 matches | ✅ | ⬜ pending |
| 3-01-02 | 01 | 1 | FR-2 | grep | `grep -n "SEARCH_EXPAND_QUERY" prompts/__init__.py` returns 0 matches | ✅ | ⬜ pending |
| 3-01-03 | 01 | 1 | FR-2 | grep | `grep -n "get_draft_llm" search_agent.py` returns 0 matches | ✅ | ⬜ pending |
| 3-02-01 | 02 | 1 | FR-2 | grep | `grep -n "search_min_confidence" config.py` shows 0.15 | ✅ | ⬜ pending |
| 3-02-02 | 02 | 1 | FR-2 | grep | `grep -n "min_relevance_score" config.py` shows 0.25 | ✅ | ⬜ pending |
| 3-03-01 | 03 | 2 | FR-2 | grep | `grep -n "p50" search_agent.py` returns matches | ✅ | ⬜ pending |
| 3-03-02 | 03 | 2 | FR-2 | grep | `grep -n "retrieval_metrics" analyze_agent.py` returns matches | ✅ | ⬜ pending |
| 3-04-01 | 04 | 3 | FR-2 | eval | `python tests/eval/run_eval.py` shows retrieval_recall_at_5 ≥ 0.80 | ✅ | ⬜ pending |

---

## Wave 0 Requirements

Existing infrastructure covers all phase requirements. No new test files needed — changes are verified by grep checks and the existing eval runner.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Retrieval returns docs without query expansion | FR-2 | Requires live Qdrant + indexed documents | Run `GET /api/v1/debug/retrieval?q=порядок+открытия+вклада`, confirm vector stage returns results |
| analyze_agent shows INFO logs in production | FR-2 | Requires running system | Send analyze request, check logs for `analyze: retrieve_and_rerank` at INFO level |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 120s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending

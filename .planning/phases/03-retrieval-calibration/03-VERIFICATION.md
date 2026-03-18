---
phase: 03-retrieval-calibration
verified: 2026-03-18T00:00:00Z
status: human_needed
score: 14/14 automated must-haves verified
re_verification: false
human_verification:
  - test: "Run eval suite against live stack: python tests/eval/run_eval.py --timeout 120"
    expected: "retrieval_recall_at_5 >= 0.80; no regression in routing_accuracy or json_validity_rate"
    why_human: "Eval requires running API server (Qdrant, Neo4j, Ollama, FastAPI). Server was not running during code changes. Task 2 of plan 04 was explicitly deferred for this reason."
---

# Phase 3: Retrieval Calibration Verification Report

**Phase Goal:** Calibrate retrieval thresholds and observability to reduce false negatives on Russian legal/banking text — specifically: remove query expansion, lower thresholds, bring analyze_agent to parity with search_agent, and validate retrieval_recall_at_5 >= 0.80.
**Verified:** 2026-03-18
**Status:** human_needed — all code changes verified, live eval run pending
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Query expansion LLM call removed — no 7b model invocation before embedding | VERIFIED | `_expand_query` absent (0 grep matches); `get_draft_llm` absent; `SEARCH_EXPAND_QUERY` absent from both search_agent.py and prompts/__init__.py |
| 2 | `search_min_confidence` threshold is 0.15, not 0.25 | VERIFIED | config.py line 128: `search_min_confidence: float = 0.15` |
| 3 | `min_relevance_score` threshold is 0.25, not 0.35 | VERIFIED | config.py line 98: `min_relevance_score: float = 0.25` |
| 4 | Both threshold settings have calibration rationale comments with date | VERIFIED | config.py lines 95-97 ("Lowered from 0.35... Calibrated 2026-03-17") and lines 125-127 ("Lowered from 0.25... Calibrated 2026-03-17") |
| 5 | `_prepare_query` returns `(query, query)` with referential enrichment preserved | VERIFIED | search_agent.py line 148: `def _prepare_query`, line 156: `return query, query`; DOC_REF_RE enrichment code present |
| 6 | `query_expanded` fully removed from entire codebase | VERIFIED | 0 matches across entire langgraph-agent directory including state.py, API endpoints, chainlit_app.py |
| 7 | `_score_stats` helper in search_agent uses `statistics.median` for p50 | VERIFIED | search_agent.py line 23: `import statistics as _statistics`; line 135: `def _score_stats`; uses `_statistics.median` |
| 8 | search_node metrics include `vector_score_stats` and `rerank_score_stats` | VERIFIED | search_agent.py lines 627-628: both keys present in metrics dict, each calling `_score_stats` with live data |
| 9 | analyze_agent `_retrieve_and_rerank` applies `min_relevance_score` pre-filter with fallback | VERIFIED | analyze_agent.py lines 198-204: cosine pre-filter with `logger.warning` fallback; mirrors search_agent pattern |
| 10 | analyze_agent logs at INFO level (not DEBUG) for retrieve_and_rerank | VERIFIED | analyze_agent.py line ~225: `logger.info("analyze: retrieve_and_rerank hits=..."`; 0 debug matches for this call |
| 11 | analyze_agent `_retrieve_and_rerank` returns 4 values; all 3 call sites updated | VERIFIED | line 238: `return reranked, best_score, has_context, stage_counts`; lines 543-544 (compare), 570, 578 (summary+qa) all unpack 4 values |
| 12 | Both analyze_node return paths include full per-stage metrics | VERIFIED | No-context path (lines 604-612) and success path (lines 652-660) both have vector_hits, bm25_hits, graph_hits, merged_hits, reranked_hits, best_rerank_score, rerank_score_stats; old `"hits"` key absent |
| 13 | 10 probing entries in dataset.json with `category=retrieval`, `expected_intent=search`, real filenames | VERIFIED | 10 probe entries (probe-01 to probe-10); 18 retrieval category entries total; expected_docs contain real filenames (depozity.pdf, zakon_o_bankah.pdf, etc.); 0 PLACEHOLDER strings |
| 14 | dataset.json total is 41 entries | VERIFIED | `grep -c '"id":' dataset.json` = 41 |

**Score:** 14/14 automated truths verified

---

## Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `app/agents/search_agent.py` | Query expansion removed; `_score_stats` helper; metrics with score stats | VERIFIED | `_expand_query` deleted; `_prepare_query` returns (query,query); `_score_stats` at line 135; `vector_score_stats`/`rerank_score_stats` in metrics dict |
| `app/prompts/__init__.py` | `SEARCH_EXPAND_QUERY` deleted; `SEARCH_EXPERT` preserved | VERIFIED | 0 matches for SEARCH_EXPAND_QUERY; SEARCH_EXPERT at line 28 |
| `app/core/config.py` | `min_relevance_score=0.25`, `search_min_confidence=0.15` with rationale comments | VERIFIED | Both values confirmed at lines 98 and 128; 2 "Calibrated 2026-03-17" comments; rerank_top_k=7, rerank_candidate_pool=30 unchanged |
| `app/agents/analyze_agent.py` | Retrieval parity: `_score_stats`, pre-filter, INFO logging, `stage_counts`, expanded metrics | VERIFIED | All 6 elements confirmed; both return paths have identical metrics keys |
| `tests/eval/dataset.json` | 41 entries; 10 probe entries with `category=retrieval`; real filenames in `expected_docs` | VERIFIED | 41 total; 10 probes; 18 retrieval entries; no placeholders |

---

## Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `search_agent _prepare_query` | `search_node` | Returns `(query, query)` — both paths use original query | WIRED | line 156: `return query, query`; call site uses tuple unpack; no LLM call in path |
| `_score_stats` | `search_node metrics dict` | `vector_score_stats` and `rerank_score_stats` keys | WIRED | Lines 627-628 call `_score_stats` with live data from `vector_hits` and `reranked` |
| `analyze_agent _retrieve_and_rerank` | `analyze_node retrieval_metrics` | 4th return value `stage_counts` dict | WIRED | 3 call sites all unpack 4 values; `stage_counts.get(...)` consumed in both metrics return paths |
| `analyze_agent min_relevance_score filter` | `config.py min_relevance_score` | `s.min_relevance_score` | WIRED | analyze_agent.py line 198: `h.get("score", 0) >= s.min_relevance_score` |
| `dataset.json probe entries` | `run_eval.py` retrieval recall metric | `category=retrieval` filter | WIRED | run_eval.py line 292: `--category` arg; line 167: `retrieval_recall` computed from category=retrieval entries only |

---

## Requirements Coverage

| Requirement | Source Plans | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| FR-2 | 03-01, 03-02, 03-03, 03-04 | Retrieval — No False Negatives: thresholds calibrated, query expansion removed, diagnostic observability | SATISFIED (code) — NEEDS LIVE EVAL for recall gate | All FR-2 code checkboxes satisfied. Recall >= 0.80 requires live eval. |

No orphaned requirements — FR-2 is the only requirement mapped to Phase 3 and all 4 plans claim it. REQUIREMENTS.md shows FR-2 sub-items for threshold calibration and query expansion removal as `[x]` (complete). The live recall validation is the remaining open item.

---

## Anti-Patterns Found

None detected across 5 modified files (search_agent.py, prompts/__init__.py, config.py, analyze_agent.py, dataset.json). No TODOs, FIXMEs, placeholders, stub returns, or orphaned imports.

---

## Commit Verification

All 7 documented commits verified present in nested repo (`documentologist-miran-service--neo4j/.git`):

| Commit | Plan | Description |
|--------|------|-------------|
| `93d28cc` | 03-02 | feat: add score statistics to search_node retrieval metrics |
| `434b651` | 03-01 | feat: delete SEARCH_EXPAND_QUERY prompt constant |
| `0a56eaa` | 03-01 | feat: lower retrieval thresholds for Russian legal text |
| `a7b0273` | 03-01 | fix: remove orphaned query_expanded field from state and API init |
| `e91f92a` | 03-03 | feat: extend analyze_agent _retrieve_and_rerank with pre-filter, INFO logging, stage_counts |
| `8018d78` | 03-03 | feat: expand analyze_node retrieval_metrics with per-stage counts and score stats |
| `e21016e` | 03-04 | feat: add 10 probing retrieval entries to eval dataset |

---

## Human Verification Required

### 1. Live Eval Suite — Phase Gate

**Test:** Start the full stack (Qdrant, Neo4j, Ollama, FastAPI) and run:
```bash
cd documentologist-miran-service--neo4j/langgraph-agent
python tests/eval/run_eval.py --timeout 120
```
Or retrieval-only:
```bash
python tests/eval/run_eval.py --category retrieval --timeout 60
```

**Expected:**
- `retrieval_recall_at_5 >= 0.80` (phase 3 gate from ROADMAP.md UAT)
- `routing_accuracy` — no regression vs Phase 1 baseline
- `json_validity_rate` — no regression vs Phase 2 baseline
- No Python exceptions or crashes

**Why human:** Eval requires running API server. The server was not running during code changes. Task 2 of plan 04 explicitly deferred this step.

**If retrieval_recall_at_5 < 0.80:** Check `tests/eval/results.json` for per-entry failures. Most likely cause: probe entry `expected_docs` filenames were sourced from existing retrieval entries (retrieval-01 to retrieval-08) rather than from a live document index query. Verify filenames against actual Qdrant collection contents via `GET /api/v1/debug/retrieval?q=банк`.

---

## Gaps Summary

No blocking code gaps. All 14 automated must-haves pass across all 4 plans and 7 commits. The phase goal is structurally complete — the code changes that should reduce false negatives are in place and wired correctly. The single outstanding item is the live eval run (phase gate: `retrieval_recall_at_5 >= 0.80`) which requires the full deployed stack. This is a UAT gate, not a code gap.

---

_Verified: 2026-03-18_
_Verifier: Claude (gsd-verifier)_

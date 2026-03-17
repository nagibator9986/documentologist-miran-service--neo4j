# Phase 3: Retrieval Calibration - Research

**Researched:** 2026-03-17
**Domain:** RAG retrieval pipeline — threshold calibration, observability, query expansion removal, eval validation
**Confidence:** HIGH (all findings are from direct source-code inspection)

## Summary

This phase is a surgical brownfield change with no new libraries required. All implementation is confined to four existing files plus the eval dataset. The codebase is already well-structured: search_agent has a pattern (retrieval_metrics INFO log, `_filter_by_relevance`, `_rerank_and_calibrate`) that must be mirrored into analyze_agent. The changes are additive except for two deletions (`_expand_query` function and `SEARCH_EXPAND_QUERY` prompt).

The central insight from reading the code: analyze_agent's `_retrieve_and_rerank` does NOT have the `min_relevance_score` pre-filter that search_agent's `_filter_by_relevance` provides, and its log sits at DEBUG not INFO. This asymmetry is the primary gap this phase closes. The cross-encoder sigmoid in reranker.py is already correct — no changes needed there.

The eval dataset currently has 8 retrieval entries (retrieval-01 through retrieval-08). All use placeholder filenames (e.g. `zakon_o_bankah.pdf`) that will need to match what is actually indexed in the `bank_knowledge` collection. The eval runner computes `retrieval_recall_at_5` by checking if any expected filename appears in the `citations` array. Adding 10 new probing entries must follow the same schema.

**Primary recommendation:** Work strictly in the order: (1) delete _expand_query + prompt, (2) update config thresholds with comments, (3) add score statistics helper, (4) update search_agent metrics, (5) wire analyze_agent to match, (6) add probing entries to dataset.json. Each step is independently verifiable.

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Query Expansion**
- Remove `_expand_query` entirely from `search_agent.py` — the 7b LLM rewrite before embedding is a confirmed false-negative risk (rewrites can drop key legal terms)
- Delete the `SEARCH_EXPAND_QUERY` prompt from `app/prompts/__init__.py`
- Remove the now-unused `get_draft_llm` import from `search_agent.py`
- Keep referential enrichment (filename prepend when query contains "этот документ") — it's cheap (no LLM call) and helps document-specific queries
- Remove `query_expanded` field from `search_node`'s `retrieval_metrics` dict (always False after removal; dead field is noise)

**Threshold Calibration**
- `search_min_confidence`: 0.25 → 0.15 — lower the post-rerank confidence gate; cross-encoder sigmoid scores on Russian legal text average 0.15-0.35, original 0.25 was cutting borderline-relevant documents
- `min_relevance_score`: 0.35 → 0.25 — lower the cosine pre-filter; gives the cross-encoder more candidates in the normal path, not just the fallback path
- Both new values must have rationale comments in `config.py` explaining why they were chosen (not just the values)
- `rerank_top_k` (7) and `rerank_candidate_pool` (30) stay unchanged
- No POST /api/v1/debug/retrieval — GET endpoint from Phase 1 is sufficient; POST adds no real value

**Analyze Agent Retrieval Logging**
- Promote `_retrieve_and_rerank` log from DEBUG to INFO — make analyze retrieval visible in production logs at the same level as search_node metrics
- Add `retrieval_metrics` dict to `analyze_node` (mirrors search_node pattern: vector_hits, bm25_hits, graph_hits, merged_hits, reranked_hits, best_rerank_score, has_context, elapsed_s)
- Add `min_relevance_score` pre-filter with fallback to `_retrieve_and_rerank` — consistent behavior with search_agent's `_filter_by_relevance` (if all hits filtered, use all)
- Log compare task both sides: when `_retrieve_compare_pair` calls `_retrieve_and_rerank` twice, log side_a and side_b metrics separately (best_score_a, best_score_b, hits_a, hits_b)

**Per-Stage Score Statistics**
- Add min/max/p50 score statistics to INFO-level metrics in both search_agent and analyze_agent — not just counts
- For vector stage: min/max/p50 of cosine scores
- For reranked stage: min/max/p50 of rerank_score (sigmoid)
- Distinguishes "all scores ~0.2" from "3 good hits + 37 noise" — critical for threshold calibration

**Probing Query Validation**
- Add 10 probing entries to `tests/eval/dataset.json` with `"category": "retrieval"` and `"expected_doc"` field (placeholder filenames — executor fills based on indexed collection)
- Use generic banking/legal domain queries (порядок открытия вклада, ставка рефинансирования, требования к заёмщику, etc.)
- All use default collection (bank_knowledge) — no collection field override needed
- Eval runner is the UAT gate: plan requires running `python tests/eval/run_eval.py` after changes and confirming `retrieval_recall@5 ≥ 0.80` with no regression in `routing_accuracy` or `json_validity_rate`

### Claude's Discretion
- Exact score statistics function implementation (percentile calculation approach)
- Format of side_a / side_b logging in compare tasks
- Whether to add score stats to debug endpoint response or just to logs

### Deferred Ideas (OUT OF SCOPE)
- Analyze double-retrieval fix (`_retrieve_compare_pair` + `_retrieve_and_rerank` graph deduplication) — deferred to Phase 4
- POST /api/v1/debug/retrieval — skipped; GET endpoint from Phase 1 is sufficient
- A/B test query expansion — decision: remove entirely, no need to test both paths
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| FR-2 | Retrieval — No False Negatives: если документ проиндексирован в Qdrant, агент должен его находить при прямом вопросе; пороги `min_relevance_score` и `search_min_confidence` откалиброваны | Threshold changes (0.25→0.15, 0.35→0.25) + removal of _expand_query false-negative source + min_relevance pre-filter in analyze_agent + eval gate recall@5 ≥ 0.80 |
</phase_requirements>

---

## Standard Stack

No new libraries. All changes use the existing stack.

### Core (already installed)
| Library | Version | Purpose | Role in Phase |
|---------|---------|---------|---------------|
| sentence-transformers | existing | CrossEncoder inference | reranker.py — read-only this phase |
| pydantic-settings | existing | Config/Settings | config.py threshold changes |
| fastapi | existing | API layer | debug.py — read-only this phase |
| langchain-core | existing | Tool invocation pattern | unchanged |

### Score Statistics Helper — Implementation Guidance

The planner has discretion on approach. The existing `_percentile` function in `run_eval.py` uses linear interpolation and is a clean reference. For inline use in agents, a simpler `statistics.median` + `min`/`max` pattern avoids importing the eval module:

```python
import statistics

def _score_stats(scores: list[float]) -> dict:
    """Return min/max/p50 stats for a score list. Returns zeros if empty."""
    if not scores:
        return {"min": 0.0, "max": 0.0, "p50": 0.0}
    return {
        "min": round(min(scores), 4),
        "max": round(max(scores), 4),
        "p50": round(statistics.median(scores), 4),
    }
```

`statistics` is stdlib — no new dependency. This function can be defined at module level in both agents and called when building the metrics dict.

**Installation:** None required.

---

## Architecture Patterns

### Existing Pattern: search_node retrieval_metrics (COPY THIS INTO analyze_node)

The current search_node metrics dict (lines 632-651 of search_agent.py) is the reference pattern for analyze_node. The planner must expand it to include score stats:

```python
# Current search_node metrics (lines 632-651)
metrics = {
    "node": "search",
    "intent": state.get("intent", ""),
    "tier": state.get("tier", ""),
    "query_len": len(lexical_query),
    "query_expanded": embed_query != lexical_query,   # DELETE this field
    "vector_hits": len(vector_hits),
    "bm25_hits": len(bm25_hits),
    "graph_hits": len(graph_hits),
    "entity_hits": len(graph_results.get("entities", [])),
    "law_hits": len(graph_results.get("laws", [])),
    "merged_hits": len(merged),
    "relevant_hits": len(relevant),
    "reranked_hits": len(reranked),
    "best_rerank_score": round(best_score, 4),
    "has_context": has_context,
    "is_exact_search": is_exact,
    "elapsed_s": round(elapsed, 2),
}
logger.info("search_node metrics: %s", metrics)
```

After Phase 3, search_node metrics must ADD:
- `vector_score_stats`: `_score_stats([h.get("score", 0) for h in vector_hits])`
- `rerank_score_stats`: `_score_stats([h.get("rerank_score", 0) for h in reranked])`
- DELETE `"query_expanded"` key

### Existing Pattern: analyze_node current metrics (EXPAND THIS)

The current analyze_node returns a minimal `retrieval_metrics` dict with only: node, intent, tier, task, hits, best_score, has_context, json_parse_success, elapsed_s.

Phase 3 must expand this to include: vector_hits, bm25_hits, graph_hits, merged_hits, reranked_hits, vector_score_stats, rerank_score_stats.

The `_retrieve_and_rerank` function must return these counts. Its current signature:
```python
def _retrieve_and_rerank(search_query, lexical_query, limit, s) -> tuple[list[dict], float, bool]:
```
Must become (or supplement via a richer return value):
```python
# Option A: return additional counts dict
def _retrieve_and_rerank(...) -> tuple[list[dict], float, bool, dict]:
    # dict = {"vector_hits": N, "bm25_hits": N, "graph_hits": N, "merged_hits": N}
```

### Existing Pattern: _filter_by_relevance (COPY INTO _retrieve_and_rerank)

The search_agent's `_filter_by_relevance` (lines 453-462) is the template:

```python
def _filter_by_relevance(hits: list[dict], s: Settings) -> list[dict]:
    filtered = [h for h in hits if h.get("score", 0) >= s.min_relevance_score]
    if not filtered and hits:
        logger.warning(
            "search: all %d hits below min_relevance_score=%.2f — using all",
            len(hits), s.min_relevance_score,
        )
        return hits
    return filtered
```

This must be applied in `_retrieve_and_rerank` after step 3 (graph merge, before candidates sort), exactly mirroring the search pipeline's stage 8 position. The fallback (use all if everything filtered) is mandatory — prevents context blackout.

### Pattern: analyze compare side_a / side_b logging

Currently `_retrieve_compare_pair` in analyze_agent (lines 232-256) returns raw vector hits without reranking — reranking happens in `analyze_node` by calling `_retrieve_and_rerank` with each subject query. The current code (lines 511-516) calls `_retrieve_and_rerank` twice and merges results. The logging pattern for compare tasks:

```python
# After both sides retrieved and reranked:
logger.info(
    "analyze compare: side_a hits=%d best_score_a=%.3f | side_b hits=%d best_score_b=%.3f",
    len(reranked_a), score_a, len(reranked_b), score_b,
)
```

This satisfies the "log compare task both sides" decision without a structural change to the return type.

### Anti-Patterns to Avoid

- **Changing reranker.py sigmoid logic:** The sigmoid implementation is correct (`1/(1+exp(-x))`). Reranker.py is read-only this phase — CONTEXT.md says "already correct — read for context only."
- **Removing the fallback in _filter_by_relevance:** The `if not filtered: return hits` fallback is load-bearing. Without it, a borderline-scored collection causes zero context and an honest "not found" response — which is exactly the false-negative scenario this phase fixes.
- **Changing analyze_compare_limit or analyze_summary_limit:** These are out of scope. Only `search_min_confidence` and `min_relevance_score` are calibrated.
- **Modifying the eval runner (run_eval.py):** The runner is frozen. Only dataset.json gets new entries.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Percentile calculation | Custom sort-based percentile | `statistics.median` for p50 | stdlib, already used in run_eval.py |
| Score statistics | Complex rolling stats | Simple `min/max/statistics.median` at log time | One-liner, no overhead, no state |
| Debug retrieval stages | New endpoint or tool | Existing `GET /api/v1/debug/retrieval` | Already returns all stages + thresholds |
| Referential enrichment | LLM-based expansion | Existing `DOC_REF_RE` + `extract_recent_filename` | Already implemented, no LLM cost |

---

## Common Pitfalls

### Pitfall 1: _prepare_query returns two values — both become lexical_query after deletion

**What goes wrong:** After removing `_expand_query`, `_prepare_query` returns `(query, query)` — both return values are identical. The search_node call `lexical_query, embed_query = _prepare_query(...)` still works, but `embed_query` is now redundant. The call site `_retrieve_vector_bm25(embed_query, lexical_query, s)` will pass the same string twice.
**Why it happens:** The function signature is preserved for minimal diff, but the semantic distinction is gone.
**How to avoid:** In search_node, simplify: after removing `_expand_query`, `_prepare_query` can just return `query` once, and both vector and lexical search use the same query. The planner should decide whether to collapse to a single return value or keep the tuple with identical values.
**Warning signs:** `embed_query != lexical_query` will always be False — confirm `query_expanded` field is removed from metrics.

### Pitfall 2: analyze_node has TWO metrics dict construction sites

**What goes wrong:** analyze_node has an early-exit path (no-context, lines 542-563) that also constructs and returns `retrieval_metrics`. When adding new fields to the metrics dict, both the early-exit dict AND the success-path dict must be updated.
**Why it happens:** The early exit at line 537 (`if not has_context:`) returns immediately with a separate dict — it's easy to update only the success path.
**How to avoid:** Both dicts must gain the same new keys. The early-exit path won't have per-stage counts, so use 0 for hit counts and empty stats: `{"min": 0.0, "max": 0.0, "p50": 0.0}`.
**Warning signs:** Missing keys in retrieval_metrics when has_context is False — check both return paths.

### Pitfall 3: _retrieve_and_rerank signature change breaks analyze_node call sites

**What goes wrong:** `_retrieve_and_rerank` is called in three places in analyze_node: summary path (line 522), qa/extract path (line 530), and compare path (lines 511-512). If the return signature is extended (e.g. to return counts), all three call sites must be updated.
**Why it happens:** Python functions have single return shapes — adding a 4th element to the tuple breaks unpacking at all call sites.
**How to avoid:** Either (a) return a named dict as a 4th element and unpack consistently, or (b) collect stage counts inside `_retrieve_and_rerank` and include them in a structured return. The simplest approach: return a `counts` dict as 4th element: `(reranked, best_score, has_context, counts)`.
**Warning signs:** `TypeError: cannot unpack non-sequence` at any of the three call sites.

### Pitfall 4: dataset.json probing entries with placeholder filenames will fail retrieval check

**What goes wrong:** The eval runner checks if `expected_docs` filenames appear in `citations`. If the probing entries use placeholder filenames (e.g. `vklad_poryadok.pdf`) that don't exist in the indexed collection, retrieval_hit will always be False and recall@5 will drop.
**Why it happens:** CONTEXT.md says "placeholder filenames — executor fills based on indexed collection." The researcher/planner cannot know actual indexed filenames; this must be a task for the implementer.
**How to avoid:** In the plan, the probing-entries task must include a step: "Query the debug endpoint or list_documents endpoint to get actual filenames in bank_knowledge collection, then fill `expected_docs` accordingly." The probing entries should be written with empty `expected_docs: []` initially, or with a note that they must be filled by the implementer.
**Warning signs:** All 10 new retrieval entries show `retrieval_hit: false` even after threshold changes.

### Pitfall 5: Eval runner uses `category: "retrieval"` filter — new entries must match

**What goes wrong:** The eval runner's `--category retrieval` filter selects entries where `entry.get("category") == "retrieval"`. New probing entries must have exactly `"category": "retrieval"` — not `"retrieval-probe"` or any other variant.
**Why it happens:** String equality match in run_eval.py line 36.
**How to avoid:** Copy the schema from existing retrieval entries (retrieval-01 through retrieval-08). Required fields: `id`, `query`, `expected_intent`, `expected_docs`, `expected_json_valid`, `category`, `tags`.

---

## Code Examples

Verified patterns from direct source inspection.

### Score Statistics Helper (new function, both agents)

```python
# Add at module level in search_agent.py and analyze_agent.py
import statistics as _statistics

def _score_stats(scores: list[float]) -> dict:
    """Return min/max/p50 for a list of scores. Returns zeros if empty."""
    if not scores:
        return {"min": 0.0, "max": 0.0, "p50": 0.0}
    return {
        "min": round(min(scores), 4),
        "max": round(max(scores), 4),
        "p50": round(_statistics.median(scores), 4),
    }
```

### Config Changes with Rationale Comments

```python
# In config.py Settings class:

# Minimum sigmoid-normalised rerank score to consider context reliable.
# Lowered from 0.25: cross-encoder sigmoid on Russian legal text typically
# produces scores 0.15-0.35 for genuinely relevant docs. The original 0.25
# threshold was cutting borderline-relevant results. Calibrated 2026-03-17.
search_min_confidence: float = 0.15

# Minimum cosine similarity score to consider a hit relevant (0–1).
# Lowered from 0.35: gives cross-encoder more candidates to work with in
# the normal path (not just the fallback path). Russian legal text embeddings
# from bge-m3 cluster in the 0.25-0.45 range for relevant passages.
# Calibrated 2026-03-17.
min_relevance_score: float = 0.25
```

### _prepare_query After _expand_query Removal

```python
def _prepare_query(query: str, state: AgentState) -> str:
    """Enrich referential queries. Returns cleaned query for both lexical and vector search."""
    query = strip_conversational_prefix(query)
    if DOC_REF_RE.search(query):
        filename = extract_recent_filename(state)
        if filename:
            query = f"{filename} {query}"
            logger.info("search: referential query enriched with filename=%s", filename)
    return query
```

Note: The planner may choose to keep the two-value return `(query, query)` for a smaller diff, or collapse to a single return. Either is valid as long as `query_expanded` is removed from metrics.

### min_relevance_score Pre-filter in _retrieve_and_rerank (analyze_agent)

This must be inserted after step 4 (merge) and before step 5 (candidates sort), mirroring search_agent's stage 8:

```python
# After: merged list is assembled from bm25_hits + graph_hits
# Add this block before: candidates = sorted(merged, ...)[:s.rerank_candidate_pool]

filtered = [h for h in merged if h.get("score", 0) >= s.min_relevance_score]
if not filtered and merged:
    logger.warning(
        "analyze: all %d hits below min_relevance_score=%.2f — using all",
        len(merged), s.min_relevance_score,
    )
    filtered = merged
merged = filtered  # rebind for candidate sort below
```

### Extended _retrieve_and_rerank Return Signature

```python
def _retrieve_and_rerank(
    search_query: str,
    lexical_query: str,
    limit: int,
    s: Settings,
) -> tuple[list[dict], float, bool, dict]:
    """Returns (reranked_docs, best_score, has_context, stage_counts)"""
    # ... existing logic ...
    stage_counts = {
        "vector_hits": len(vector_hits),
        "bm25_hits": len(bm25_hits),
        "graph_hits": len(graph_hits),
        "merged_hits": len(merged),  # after filter, before candidate pool
        "reranked_hits": len(reranked),
    }
    return reranked, best_score, has_context, stage_counts
```

### Probing Entry Schema (dataset.json)

```json
{
  "id": "probe-01",
  "query": "порядок открытия депозитного вклада физическим лицом",
  "expected_intent": "search",
  "expected_docs": [],
  "expected_json_valid": null,
  "category": "retrieval",
  "tags": ["search", "retrieval", "deposits", "probe"]
}
```

Note: `expected_docs` must be filled by the implementer after checking which filenames are actually indexed in the `bank_knowledge` collection. IDs should be `probe-01` through `probe-10` to avoid colliding with existing `retrieval-01` through `retrieval-08`.

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| LLM query expansion (7b model rewrite) | Direct query embedding (no expansion) | Phase 3 | Removes 7b LLM call per request; eliminates legal term rewrite as false-negative source |
| analyze_node retrieval at DEBUG level | analyze_node retrieval at INFO level | Phase 3 | Retrieval diagnostics visible in production logs alongside search_node |
| analyze_agent: no cosine pre-filter | analyze_agent: min_relevance_score pre-filter with fallback | Phase 3 | Consistent behavior between search_agent and analyze_agent pipelines |
| Metrics: counts only | Metrics: counts + min/max/p50 per stage | Phase 3 | Distinguishes "all low scores" from "bimodal: few good + many noise" |

**Deprecated/outdated after Phase 3:**
- `_expand_query` function in search_agent.py — deleted
- `SEARCH_EXPAND_QUERY` constant in app/prompts/__init__.py — deleted
- `get_draft_llm` import in search_agent.py — removed (no other callers in this file)
- `query_expanded` key in search_node retrieval_metrics — removed (always False, dead field)

---

## Open Questions

1. **Actual filenames in bank_knowledge collection**
   - What we know: Eval entries retrieval-01 through retrieval-08 use placeholder filenames (`zakon_o_bankah.pdf`, `depozity.pdf`, etc.)
   - What's unclear: Whether these match actual indexed documents. Existing retrieval entries already have `expected_docs` set; they may already be failing.
   - Recommendation: Implementer must run `GET /api/v1/debug/retrieval?q=test` or `GET /api/v1/documents` to list actual collection contents, then fill `expected_docs` in the 10 new probe entries accordingly. Existing entries (retrieval-01 through -08) may also need their filenames verified.

2. **Signature change impact on analyze_node early-exit path**
   - What we know: analyze_node has two return sites — success path and no-context early exit (line 537)
   - What's unclear: If `_retrieve_and_rerank` returns 4 values but no-context case fires before any retrieval, the early-exit path is hit before `_retrieve_and_rerank` is even called (see analyze_node flow: retrieval happens at lines 506-534 before the `if not has_context` check at line 537). Stage counts will be available.
   - Recommendation: Both `retrieval_metrics` dicts in analyze_node must include stage counts; the no-context one gets the counts from `_retrieve_and_rerank` calls that already completed.

3. **compare path: `_retrieve_compare_pair` also calls `qdrant_search` directly**
   - What we know: In analyze_node compare branch (lines 506-518), `_retrieve_compare_pair` runs first (qdrant_search per side), then `_retrieve_and_rerank` is called twice with those results... wait — actually `_retrieve_and_rerank` does its OWN qdrant_search internally. The compare path runs `_retrieve_compare_pair` but its results (`hits_a`, `hits_b`) are not passed to `_retrieve_and_rerank`. The `_retrieve_and_rerank` calls use `query_a`/`query_b` directly and do fresh vector searches. `_retrieve_compare_pair` results are currently unused in the rerank path.
   - What's unclear: Is `_retrieve_compare_pair` currently dead code in the compare path? Looking at lines 506-518: `hits_a, hits_b, used_pair = _retrieve_compare_pair(...)` — these variables are not used further. The reranking uses independently retrieved hits. This is the "double-retrieval" issue deferred to Phase 4.
   - Recommendation: Phase 3 does not fix this. Leave `_retrieve_compare_pair` call in place. The logging should note `hits_a`/`hits_b` from the `_retrieve_and_rerank` calls (the ones that actually feed the LLM), not from `_retrieve_compare_pair`.

---

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (existing) + custom eval runner |
| Config file | `documentologist-miran-service--neo4j/langgraph-agent/tests/eval/run_eval.py` |
| Quick run command | `python tests/eval/run_eval.py --category retrieval --timeout 60` |
| Full suite command | `python tests/eval/run_eval.py --timeout 120` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| FR-2 | retrieval_recall@5 ≥ 0.80 | integration (live API) | `python tests/eval/run_eval.py --category retrieval` | ✅ run_eval.py exists; dataset.json needs 10 new entries |
| FR-2 | min_relevance_score pre-filter in analyze_agent | unit/manual | Manual: send analyze query, check INFO logs for stage counts | ❌ no unit test — acceptable for brownfield config change |
| FR-2 | _expand_query removed, no import errors | smoke | `python -c "from app.agents.search_agent import search_node"` | ✅ (after change) |
| FR-2 | Config thresholds updated | smoke | `python -c "from app.core.config import get_settings; s=get_settings(); assert s.search_min_confidence == 0.15"` | ✅ (after change) |

### Sampling Rate
- **Per task commit:** `python -c "from app.agents.search_agent import search_node"` (import smoke test)
- **Per wave merge:** `python tests/eval/run_eval.py --category retrieval --timeout 60`
- **Phase gate:** Full suite `python tests/eval/run_eval.py` green before `/gsd:verify-work`. Must show `retrieval_recall@5 ≥ 0.80` with no regression in `routing_accuracy` or `json_validity_rate`.

### Wave 0 Gaps
- [ ] 10 new probing entries in `tests/eval/dataset.json` — covers FR-2 probing queries (probe-01 through probe-10). `expected_docs` must be filled by implementer after checking actual indexed filenames.

---

## Sources

### Primary (HIGH confidence)
- Direct source read: `app/agents/search_agent.py` — full pipeline, _expand_query, _filter_by_relevance, _rerank_and_calibrate, retrieval_metrics dict (lines 632-651), _prepare_query return contract
- Direct source read: `app/agents/analyze_agent.py` — _retrieve_and_rerank, _retrieve_compare_pair, analyze_node both return paths, current metrics dict
- Direct source read: `app/core/config.py` — current values: search_min_confidence=0.25, min_relevance_score=0.35, rerank_top_k=7, rerank_candidate_pool=30
- Direct source read: `app/tools/reranker.py` — sigmoid implementation, CrossEncoder usage, rerank_score/rerank_logit fields
- Direct source read: `app/prompts/__init__.py` — SEARCH_EXPAND_QUERY definition (lines 55-62)
- Direct source read: `app/api/v1/debug.py` — existing GET /debug/retrieval endpoint, _top_scores helper
- Direct source read: `tests/eval/dataset.json` — 31 existing entries (8 retrieval category), schema
- Direct source read: `tests/eval/run_eval.py` — retrieval_recall@5 computation logic, category filtering, citation filename matching
- Direct source read: `.planning/config.json` — nyquist_validation key absent → treat as enabled

### Secondary (MEDIUM confidence)
- CONTEXT.md decisions — user decisions from discuss-phase, fully internalized
- STATE.md — completed phases context, key decisions log

### Tertiary (LOW confidence)
- None — all findings are from direct code inspection

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new libraries, all existing
- Architecture: HIGH — read every relevant function directly
- Pitfalls: HIGH — identified from code structure (two return paths, three call sites, placeholder filenames)
- Score stats helper: HIGH — stdlib statistics.median, same pattern as run_eval.py _percentile

**Research date:** 2026-03-17
**Valid until:** Indefinite — code is not changing between research and planning; all findings are from the actual source

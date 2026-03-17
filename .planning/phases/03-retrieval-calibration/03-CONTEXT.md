# Phase 3: Retrieval Calibration - Context

**Gathered:** 2026-03-17
**Status:** Ready for planning

<domain>
## Phase Boundary

Fix false negatives — documents exist in Qdrant but agents return "not found." Calibrate retrieval thresholds, eliminate query expansion as a false-negative source, and add per-stage observability to both search_agent and analyze_agent. Validate that recall@5 ≥ 80% via eval suite. No algorithm changes, no new data sources, no search UI changes.

</domain>

<decisions>
## Implementation Decisions

### Query Expansion
- **Remove `_expand_query` entirely** from `search_agent.py` — the 7b LLM rewrite before embedding is a confirmed false-negative risk (rewrites can drop key legal terms)
- Delete the `SEARCH_EXPAND_QUERY` prompt from `app/prompts/__init__.py`
- Remove the now-unused `get_draft_llm` import from `search_agent.py`
- **Keep referential enrichment** (filename prepend when query contains "этот документ") — it's cheap (no LLM call) and helps document-specific queries
- Remove `query_expanded` field from `search_node`'s `retrieval_metrics` dict (always False after removal; dead field is noise)

### Threshold Calibration
- `search_min_confidence`: **0.25 → 0.15** — lower the post-rerank confidence gate; cross-encoder sigmoid scores on Russian legal text average 0.15-0.35, original 0.25 was cutting borderline-relevant documents
- `min_relevance_score`: **0.35 → 0.25** — lower the cosine pre-filter; gives the cross-encoder more candidates in the normal path, not just the fallback path
- Both new values must have **rationale comments** in `config.py` explaining why they were chosen (not just the values)
- `rerank_top_k` (7) and `rerank_candidate_pool` (30) stay unchanged
- **No POST /api/v1/debug/retrieval** — GET endpoint from Phase 1 is sufficient; POST adds no real value

### Analyze Agent Retrieval Logging
- **Promote `_retrieve_and_rerank` log from DEBUG to INFO** — make analyze retrieval visible in production logs at the same level as search_node metrics
- **Add `retrieval_metrics` dict to `analyze_node`** (mirrors search_node pattern: vector_hits, bm25_hits, graph_hits, merged_hits, reranked_hits, best_rerank_score, has_context, elapsed_s)
- **Add `min_relevance_score` pre-filter with fallback** to `_retrieve_and_rerank` — consistent behavior with search_agent's `_filter_by_relevance` (if all hits filtered, use all)
- **Log compare task both sides**: when `_retrieve_compare_pair` calls `_retrieve_and_rerank` twice, log side_a and side_b metrics separately (best_score_a, best_score_b, hits_a, hits_b)

### Per-Stage Score Statistics
- Add **min/max/p50 score statistics** to INFO-level metrics in both search_agent and analyze_agent — not just counts
- For vector stage: min/max/p50 of cosine scores
- For reranked stage: min/max/p50 of rerank_score (sigmoid)
- Distinguishes "all scores ~0.2" from "3 good hits + 37 noise" — critical for threshold calibration

### Probing Query Validation
- Add **10 probing entries** to `tests/eval/dataset.json` with `"category": "retrieval"` and `"expected_doc"` field (placeholder filenames — executor fills based on indexed collection)
- Use generic banking/legal domain queries (порядок открытия вклада, ставка рефинансирования, требования к заёмщику, etc.)
- All use default collection (bank_knowledge) — no collection field override needed
- **Eval runner is the UAT gate**: plan requires running `python tests/eval/run_eval.py` after changes and confirming `retrieval_recall@5 ≥ 0.80` with no regression in `routing_accuracy` or `json_validity_rate`

### Claude's Discretion
- Exact score statistics function implementation (percentile calculation approach)
- Format of side_a / side_b logging in compare tasks
- Whether to add score stats to debug endpoint response or just to logs

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

No external specs or ADRs for this phase — requirements are fully captured in decisions above and the files listed below.

### Core files to read before touching anything
- `documentologist-miran-service--neo4j/langgraph-agent/app/agents/search_agent.py` — pipeline stages 1-12, `_expand_query`, `_filter_by_relevance`, `_rerank_and_calibrate`, `retrieval_metrics` dict
- `documentologist-miran-service--neo4j/langgraph-agent/app/agents/analyze_agent.py` — `_retrieve_and_rerank`, `_retrieve_compare_pair`, `analyze_node`
- `documentologist-miran-service--neo4j/langgraph-agent/app/core/config.py` — `search_min_confidence`, `min_relevance_score`, `rerank_top_k`, `rerank_candidate_pool`
- `documentologist-miran-service--neo4j/langgraph-agent/app/tools/reranker.py` — sigmoid normalization (already correct — read for context only)
- `documentologist-miran-service--neo4j/langgraph-agent/app/prompts/__init__.py` — `SEARCH_EXPAND_QUERY` (to delete)
- `tests/eval/dataset.json` — existing 31 entries; add 10 probing entries
- `tests/eval/run_eval.py` — eval runner; must still pass after changes

### Phase 1 artifacts (read for existing patterns)
- `documentologist-miran-service--neo4j/langgraph-agent/app/api/v1/debug.py` — existing GET debug endpoint (no changes needed)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `_filter_by_relevance` in search_agent.py: cosine pre-filter with fallback — copy this pattern into analyze_agent's `_retrieve_and_rerank`
- `search_node` retrieval_metrics dict (lines 632-651 in search_agent.py): exact structure to mirror in `analyze_node`
- `_top_scores` helper in debug.py: extracts top-N scores — can adapt for per-stage stats

### Established Patterns
- INFO-level structured metrics dict (`logger.info("X metrics: %s", metrics)`) — search_node uses this; analyze_node should match
- `rerank_candidate_pool` guard (`sorted(...candidates)[:s.rerank_candidate_pool]`) — already in both agents; don't change

### Integration Points
- `_prepare_query` in search_agent calls `_expand_query` — after deletion, `_prepare_query` returns `(query, query)` (same value for both lexical and embed)
- `embed_query` variable in `search_node` will equal `lexical_query` after the change — simplify accordingly
- `analyze_node` return dict needs `retrieval_metrics` key added

</code_context>

<specifics>
## Specific Ideas

- Config comments should be explicit: e.g., `# Lowered from 0.35: cross-encoder sigmoid scores on Russian legal text often land 0.15-0.30 for genuinely relevant docs; original threshold was cutting too many borderline-relevant results. Calibrated 2026-03-17.`
- Probing queries should cover: вклад/депозит, кредит/займ, залог/поручительство, ставка/процент, требования к документам — core banking/legal topics most likely to be in the indexed collection

</specifics>

<deferred>
## Deferred Ideas

- **Analyze double-retrieval fix** (`_retrieve_compare_pair` + `_retrieve_and_rerank` graph deduplication) — deferred to Phase 4 (Agent Logic Hardening)
- **POST /api/v1/debug/retrieval** — skipped; GET endpoint from Phase 1 is sufficient
- **A/B test query expansion** — decision: remove entirely, no need to test both paths

</deferred>

---

*Phase: 03-retrieval-calibration*
*Context gathered: 2026-03-17*

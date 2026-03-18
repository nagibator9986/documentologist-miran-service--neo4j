# Phase 4: Agent Logic Hardening - Research

**Researched:** 2026-03-18
**Domain:** LangGraph agent routing, error handling, graceful degradation
**Confidence:** HIGH

## Summary

Phase 4 hardens the existing multi-agent system by closing routing gaps, eliminating duplicate retrieval in analyze/compare, adding hallucination guards, and ensuring graceful degradation when external services (Ollama, Redis) fail. All changes are surgical edits to existing files -- no new modules, no architecture changes.

The codebase is already well-structured with clear separation of concerns. The supervisor uses a 3-tier classification (compound -> keyword -> LLM), agents have structured pipelines, and `invoke_with_retry` already provides tenacity-based retries for Ollama calls. The key gaps are: (1) keyword patterns miss banking/legal edge cases causing LLM tier fallback unnecessarily, (2) analyze compare path does double retrieval, (3) no hallucination guard exists, (4) Ollama timeout propagates as 500 error instead of graceful response, (5) Redis failure in `memory_load_node` is already non-fatal but `memory_save_node` needs verification.

**Primary recommendation:** Address tasks in dependency order: supervisor logging first (provides observability for subsequent changes), then keyword expansion + compare dedup (independent of each other), then quality guard + graceful degradation (require understanding of error propagation).

## Architecture Patterns

### Current System Architecture (relevant to Phase 4)
```
Flow: START -> memory_load -> supervisor -> {agent} -> [advance_intent?] -> memory_save -> END

Files to modify:
  app/agents/supervisor.py      — Tasks 1, 2 (keyword patterns + logging)
  app/agents/search_agent.py    — Task 4 (hallucination guard)
  app/agents/analyze_agent.py   — Task 3 (compare double-retrieval)
  app/agents/verify_agent.py    — Task 5 (graceful degradation)
  app/agents/generate_agent.py  — Task 5 (graceful degradation)
  app/agents/ingest_agent.py    — Task 5 (graceful degradation)
  app/agents/memory_agent.py    — Task 6 (Redis failure verification)
  app/core/llm.py               — Task 5 (invoke_with_retry behavior)
  app/api/v1/chat.py            — Task 5 (HTTP error handling)
  app/api/v1/completions.py     — Task 5 (HTTP error handling)
```

### Pattern: Supervisor Three-Tier Classification
```python
# Existing pattern — add patterns to _COMPOUND_PATTERNS and keyword regexes
# Order matters: compound -> keyword -> LLM
# Keyword order: ingest -> analyze -> generate -> verify -> search (broadest last)
```

### Pattern: Non-Fatal Service Calls (already established)
```python
# Memory agent already wraps Redis/PG in try/except with WARNING log
# Graph enrichment in search_agent is non-fatal with timeout
# MinIO upload in generate_agent is non-fatal
# Pattern: try operation → catch → log WARNING → return safe default
```

### Pattern: invoke_with_retry Behavior
```python
# Current: returns empty string "" on all failures (RetryError + non-retryable)
# This is the key for graceful degradation — agents already get "" on LLM failure
# BUT: agents don't check for empty response to produce graceful user message
```

### Anti-Patterns to Avoid
- **Modifying workflow.py:** Routing logic belongs in workflow.py, classification in supervisor.py. Do not mix responsibilities.
- **Adding new exception types:** Use existing try/except + WARNING log pattern. Do not create custom exception hierarchies.
- **Changing invoke_with_retry contract:** It returns "" on failure — this is intentional. Agents should handle "" downstream.

## Detailed Analysis Per Task

### Task 1: Supervisor Keyword Pattern Expansion

**Current state:** 13 routing test queries in eval dataset, all use keyword tier for clear cases. The current patterns cover basic banking/legal terms but miss edge cases.

**Identified gaps from eval dataset analysis and domain knowledge:**

Banking edge cases NOT currently covered by keyword patterns:
- "верно ли что..." -> should be verify (currently no match, falls to LLM)
- "допускается ли..." -> verify intent (not covered)
- "правомерно ли..." -> verify (partially covered: `правомерн` exists)
- "можно ли..." + legal context -> ambiguous, often verify
- "какие документы нужны для..." -> search (currently matched by `какие\s+права` but not `документы нужны`)
- "порядок погашения..." -> search (currently NOT matched -- no pattern for `порядок`)
- "условия кредитования..." -> search (not matched)
- "ставка рефинансирования..." -> search (not matched)
- "обязанности банка..." -> search (matched by `обязан` in analyze_kw? NO -- `обяза` in _OBL_KEYWORDS is only in search_agent, not supervisor)
- "резюмируй документ" -> analyze (already covered by `резюм`)

**Supervisor keyword patterns vs eval queries analysis:**
- route-08 "верно ли что банк обязан иметь резервный фонд" -> expected: verify. Current: `_VERIFY_KW` does NOT match "верно ли" -- this falls to LLM tier.
- All probing queries (probe-01..10) are search intent. Most match existing `_SEARCH_KW` patterns via `что такое`, `расскажи`, `как получить`, etc. BUT: queries like "залог имущества как обеспечение кредита" (probe-04) or "условия кредитования юридических лиц" (probe-05) have NO keyword match -- they fall to LLM.

**Recommended additions to keyword patterns:**

_VERIFY_KW additions:
- `верно\s+ли` — factual compliance check
- `допустим[оа]?\s+ли` — permissibility check
- `можно\s+ли\s+(?!.*получить|.*оформить)` — legality check (but not procedural)
- `имеет\s+ли\s+право` — rights verification
- `вправе\s+ли` — authority check
- `позволяет\s+ли\s+закон` — law permissibility

_SEARCH_KW additions:
- `порядок\s+(?!.*проверк)` — procedural queries (not verify)
- `условия\s+` — terms/conditions queries
- `требования\s+(?:к|для|по)` — requirements queries
- `ставка\s+` — rate queries
- `обязанности\s+` — obligation info queries
- `права\s+(?:вкладчик|заёмщик|клиент)` — rights info queries
- `процедура\s+` — procedure queries
- `страхование\s+` — insurance queries

_ANALYZE_KW additions:
- `проанализируй` — direct analysis command (currently covered by `резюм|суммар` etc. but "проанализируй" has no match)
- `анализ\s+(?:документа|текста|закона)` — noun-form analysis

**Confidence:** HIGH -- based on direct regex testing against eval queries and pattern analysis.

### Task 2: Supervisor Tier+Intent Logging

**Current state:** Logging ALREADY EXISTS in supervisor.py:
- Line 171: `logger.info("Supervisor: compound [%s, %s] query=%r", primary, secondary, query[:80])`
- Line 192: `logger.info("Supervisor: keyword intent=%s query=%r", kw_intent, query[:80])`
- Line 221: `logger.info("Supervisor: llm intent=%s query=%r raw=%r", intent, query[:80], raw[:40])`

**Gap:** The ROADMAP asks for the format `logger.info("supervisor: tier=%s intent=%s query=%r", tier, intent, query[:60])` -- a standardized format across all tiers. Current logging uses different formats per tier.

**Recommendation:** Normalize all three log lines to the same structured format. This is a minor cosmetic task -- 3 lines changed. Consider adding this as part of a larger task, not standalone.

**Confidence:** HIGH -- direct code inspection.

### Task 3: Analyze/Compare Double-Retrieval Fix

**Current state analysis (CRITICAL):**

In `analyze_node` when `task == "compare"` (line 535-566):
1. `_retrieve_compare_pair(query, s)` -- does 2 `qdrant_search.invoke()` calls (one per subject) or 1 fallback call
2. `_retrieve_and_rerank(query_a, ...)` -- does ANOTHER `qdrant_search.invoke()` + bm25 + graph + rerank for side A
3. `_retrieve_and_rerank(query_b, ...)` -- does ANOTHER `qdrant_search.invoke()` + bm25 + graph + rerank for side B

This means: **4 total Qdrant vector searches** for a compare query (2 from `_retrieve_compare_pair` + 2 from `_retrieve_and_rerank`). The results from `_retrieve_compare_pair` (hits_a, hits_b) are NEVER USED -- they are computed and then discarded!

Look at lines 538-543:
```python
hits_a, hits_b, used_pair = _retrieve_compare_pair(query, s)  # <- results unused!
reranked_a, score_a, ok_a, counts_a = _retrieve_and_rerank(query_a, query_a, ...)
reranked_b, score_b, ok_b, counts_b = _retrieve_and_rerank(query_b, query_b, ...)
```

**Fix:** Remove `_retrieve_compare_pair` call from the compare path. Instead, use `_extract_compare_subjects(query)` directly to get subjects, then pass them to `_retrieve_and_rerank`. The `_retrieve_compare_pair` function can be deleted entirely or its logic merged.

**Alternatively:** Modify `_retrieve_and_rerank` to accept pre-fetched hits, or refactor `_retrieve_compare_pair` to also do BM25+graph+rerank and delete the separate `_retrieve_and_rerank` calls.

**Recommended approach:** Keep `_extract_compare_subjects()` (it extracts the pair). Delete `_retrieve_compare_pair()`. Use `_retrieve_and_rerank()` directly for each side. This cuts Qdrant calls from 4 to 2.

**Confidence:** HIGH -- direct code path analysis, verified variable usage.

### Task 4: Search Agent Hallucination Guard

**Current state:** When `has_context=True` (documents found, score above threshold), the LLM gets context and generates an answer. But the LLM might still produce a "no information" response despite having context -- a hallucination-stub.

**Where to add the guard:** After `_generate_answer()` in `search_node()` (line 605), before building citations.

**Detection patterns (Russian):**
- "у меня нет информации" / "у меня нет данных"
- "не располагаю информацией"
- "в предоставленных документах не найдено"
- "не могу найти информацию"
- "к сожалению, в контексте нет"
- "в базе знаний не найдено" (when has_context=True, this is contradictory)

**Implementation pattern:**
```python
_HALLUCINATION_STUBS = re.compile(
    r"(?:у меня нет (?:информации|данных)|не располагаю информацией|"
    r"не (?:могу|удалось) найти (?:информацию|данные|ответ)|"
    r"в (?:предоставленных|загруженных) документах не найдено|"
    r"в базе знаний не найдено|"
    r"к сожалению.{0,30}(?:нет информации|не найдено))",
    re.IGNORECASE | re.UNICODE,
)

# After _generate_answer:
if has_context and _HALLUCINATION_STUBS.search(answer):
    logger.warning("search: hallucination stub detected with has_context=True, retrying")
    # Retry with more explicit prompt
    retry_msg = (
        f"Контекст:\n{context}\n\n"
        f"Вопрос: {query}\n\n"
        "ВАЖНО: Ответь на основе предоставленного контекста. "
        "Документы содержат релевантную информацию. Не говори что информации нет."
    )
    answer = _generate_answer(retry_msg, state, s)
```

**Key design decision:** Only retry ONCE. If the second attempt also stubs, accept it -- the context may genuinely not answer the question despite high similarity score.

**Confidence:** MEDIUM -- the pattern list may need tuning based on actual LLM output patterns. The mechanism is straightforward.

### Task 5: Graceful Degradation on Ollama Timeout

**Current state analysis:**

`invoke_with_retry` in `llm.py` (line 137-164) already:
- Retries on `ConnectionError`, `TimeoutError`, `OSError` with exponential backoff
- Returns `""` on `RetryError` (exhausted retries) and non-retryable exceptions
- Logs ERROR on failure

**Problem chain:**
1. Ollama timeout -> `invoke_with_retry` returns `""`
2. Agent receives `""` as LLM output
3. What happens next depends on the agent:

**search_agent:** `_generate_answer` returns `""` -> `answer = ""` -> `build_final_response("")` returns `""` -> user gets empty response (200 OK but empty body). This is BAD -- should return a meaningful message.

**verify_agent:** `parse_with_retry` handles this -- on failure returns fallback dict with `_parse_failed=True`. This already works correctly with a clear message.

**generate_agent:** `_plan_document` uses `parse_with_retry` -- handles failure with fallback dict. `_validate_draft` uses `invoke_with_retry` -- empty return -> falls back to draft (line 213). `_generate_draft` uses `doc_generate.invoke` -- this is a tool, not LLM. Overall: generate agent has partial coverage but `_generate_draft` could fail if `doc_generate` depends on Ollama internally.

**analyze_agent:** For JSON tasks, `parse_with_retry` handles it. For text tasks (qa, summary), `invoke_with_retry` returns `""` -> `_parse_result("", task)` -> `{"task": task, "result": "", "confidence": 0.0}` -> `_format_output` produces empty result. This is suboptimal.

**ingest_agent:** No LLM calls -- immune to Ollama timeout.

**supervisor:** `invoke_with_retry` returns `""` -> `_detect_intents_from_llm(query, "")` -> no valid intents found -> defaults to `["search"]`. This is acceptable graceful degradation.

**HTTP layer:** `chat.py` catches `Exception` on graph execution and returns 500. The graph itself will NOT crash if agents return empty results -- LangGraph handles state updates normally.

**The real gap is at the HTTP level.** If graph.invoke itself raises (e.g. Ollama connection refused before retry logic kicks in, or an unhandled exception type), the API returns 500. The `_run_graph_async` wrapper catches `asyncio.TimeoutError` (504) and generic `Exception` (500).

**Recommended approach:**
1. In `search_agent.search_node`: after `_generate_answer`, check for empty `answer` and replace with a graceful message.
2. In `analyze_agent._run_analysis_llm`: for text tasks, check for empty `raw` and return a graceful message.
3. In `chat.py`: change the generic Exception handler to return 200 with an error message instead of 500, since the user should see a graceful response.
4. In `completions.py`: same treatment.

**Confidence:** HIGH -- direct code path tracing.

### Task 6: Memory Agent Redis Failure Verification

**Current state analysis:**

`memory_load_node` (line 88-134):
- Wraps `session_load.invoke()` in try/except (line 98-102)
- On exception: logs WARNING, returns `state` unchanged -> **NON-FATAL, CORRECT**

`memory_save_node` (line 39-85):
- Wraps Redis saves in try/except (lines 53-59)
- On exception: logs WARNING, sets `redis_ok = False` -> continues to PG save
- PG save is also wrapped in try/except (lines 66-75) -> **NON-FATAL, CORRECT**

**BUT**: `_redis_client()` in `session_memory.py` (line 61-65) creates a NEW Redis client on every call:
```python
def _redis_client():
    import redis
    return redis.from_url(s.redis_url, decode_responses=True, socket_connect_timeout=5)
```

If Redis is unreachable, `redis.from_url()` itself may not throw (it creates a lazy client). The exception happens on the first command (`rpush`, `lrange`). With `socket_connect_timeout=5`, it will wait 5 seconds before timing out.

For `memory_save_node`, TWO Redis operations are performed sequentially (user + assistant messages). If Redis is down, this adds ~10 seconds latency (2 x 5s timeout). This is acceptable but worth noting.

**Conclusion:** Memory agent is ALREADY non-fatal on Redis failure. The task is primarily verification/confirmation. May want to add a specific test scenario to the UAT.

**Confidence:** HIGH -- direct code analysis of all exception paths.

## Common Pitfalls

### Pitfall 1: Keyword Pattern Conflicts
**What goes wrong:** Adding new patterns to `_SEARCH_KW` or `_VERIFY_KW` that overlap, causing misrouting.
**Why it happens:** Russian word stems are morphologically rich -- "обязан" matches both verify intent ("обязан ли") and search intent ("обязанности банка").
**How to avoid:** Test every new pattern against the full eval dataset (41 queries). Order matters: verify is checked BEFORE search in `_keyword_classify`.
**Warning signs:** Eval routing accuracy drops after adding patterns.

### Pitfall 2: Hallucination Guard False Positives
**What goes wrong:** The regex catches legitimate "no information" responses when documents genuinely don't answer the question.
**Why it happens:** `has_context=True` means rerank score is above threshold, but the content may be tangentially related.
**How to avoid:** Only retry once. Only flag when `has_context=True`. Do not retry on `has_context=False` (those "no info" responses are correct).

### Pitfall 3: Graceful Degradation Hiding Real Errors
**What goes wrong:** Returning 200 with a "service temporarily unavailable" message masks persistent infrastructure failures.
**Why it happens:** Graceful degradation is indistinguishable from normal operation at the HTTP status code level.
**How to avoid:** Always log ERROR when producing graceful degradation responses. Include a flag in retrieval_metrics (e.g., `"degraded": true`).

### Pitfall 4: Compare Double-Retrieval Refactor Breaking Side Contexts
**What goes wrong:** After removing `_retrieve_compare_pair`, the compare context building uses reranked results instead of raw qdrant hits. The context format may change.
**Why it happens:** `_build_compare_context` was designed to work with any hit lists.
**How to avoid:** Use reranked_a and reranked_b directly with `_build_compare_context` -- the function already accepts any list of hit dicts.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| LLM retry logic | Custom retry loops | `invoke_with_retry` (existing) | Already handles exponential backoff, error types |
| JSON parse with retry | Manual parse loops | `parse_with_retry` (existing) | Schema validation, fallback dicts, metrics |
| Redis connection pooling | Custom pool | `redis.from_url()` (existing pattern) | Sufficient for current load |

## Code Examples

### Normalized Supervisor Logging (Task 2)
```python
# Replace three different log formats with one consistent format:
logger.info("supervisor: tier=%s intent=%s query=%r", tier, intent, query[:60])
# For compound: tier="compound", intent=primary (log secondary in extra field)
```

### Graceful Degradation Response (Task 5)
```python
# In search_node, after _generate_answer:
_OLLAMA_UNAVAILABLE_MSG = (
    "К сожалению, языковая модель временно недоступна. "
    "Найденные документы доступны в цитатах ниже. "
    "Попробуйте повторить запрос через несколько минут."
)

if not answer.strip():
    logger.error("search_node: LLM returned empty response (Ollama may be down)")
    answer = _OLLAMA_UNAVAILABLE_MSG
```

### Compare Path Fix (Task 3)
```python
# BEFORE (4 Qdrant calls):
hits_a, hits_b, used_pair = _retrieve_compare_pair(query, s)
reranked_a, score_a, ok_a, counts_a = _retrieve_and_rerank(query_a, query_a, ...)
reranked_b, score_b, ok_b, counts_b = _retrieve_and_rerank(query_b, query_b, ...)

# AFTER (2 Qdrant calls):
subjects = _extract_compare_subjects(query)
query_a = subjects[0] if subjects else query
query_b = subjects[1] if subjects else query
reranked_a, score_a, ok_a, counts_a = _retrieve_and_rerank(query_a, query_a, ...)
reranked_b, score_b, ok_b, counts_b = _retrieve_and_rerank(query_b, query_b, ...)
```

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest + httpx (eval runner) |
| Config file | none -- eval runner is standalone script |
| Quick run command | `python tests/eval/run_eval.py --category routing` |
| Full suite command | `python tests/eval/run_eval.py` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| Task 1 | Routing accuracy >= 90% | eval | `python tests/eval/run_eval.py --category routing` | Yes |
| Task 2 | Tier+intent logged | manual | Check structured log output | N/A |
| Task 3 | No double retrieval | manual | Compare Qdrant call count before/after | N/A |
| Task 4 | Hallucination guard | manual | Send query where LLM stubs despite context | N/A |
| Task 5 | Ollama timeout -> 200 | manual | Stop Ollama, send request, check HTTP 200 | N/A |
| Task 6 | Redis failure non-fatal | manual | Stop Redis, send request, check HTTP 200 | N/A |

### Sampling Rate
- **Per task commit:** routing eval: `python tests/eval/run_eval.py --category routing`
- **Per wave merge:** full eval: `python tests/eval/run_eval.py`
- **Phase gate:** Full suite, routing_accuracy >= 0.90

### Wave 0 Gaps
None -- existing eval infrastructure covers routing accuracy. Manual UAT tests for degradation scenarios are defined in the ROADMAP.

## Open Questions

1. **Exact hallucination stub patterns**
   - What we know: LLM occasionally produces "no info" responses despite having context
   - What's unclear: Exact Russian text patterns the qwen2.5:14b model uses
   - Recommendation: Start with conservative pattern set, log matches, expand based on observed outputs

2. **HTTP 200 vs 503 for degraded responses**
   - What we know: ROADMAP says "ответ 200 с сообщением об ошибке"
   - What's unclear: Whether API clients (Chainlit, OpenWebUI) handle 200-with-error-message well
   - Recommendation: Use 200 per ROADMAP spec. Add `"degraded": true` in retrieval_metrics for monitoring.

3. **New routing eval entries needed?**
   - What we know: 13 routing entries exist. Adding keyword patterns needs test coverage.
   - What's unclear: Whether existing 13 entries test the new patterns
   - Recommendation: Add 5-10 routing entries for the new banking/legal edge cases

## Sources

### Primary (HIGH confidence)
- Direct codebase analysis of all agent files, llm.py, chat.py, completions.py, session_memory.py
- Eval dataset (41 entries): tests/eval/dataset.json
- Configuration: app/core/config.py (Settings class)

### Secondary (MEDIUM confidence)
- tenacity retry patterns (training data knowledge, verified against llm.py implementation)
- LangGraph StateGraph behavior (verified against workflow.py)

## Metadata

**Confidence breakdown:**
- Supervisor patterns: HIGH - direct regex analysis against eval queries
- Compare double-retrieval: HIGH - direct code path tracing, variable usage verified
- Graceful degradation: HIGH - complete error propagation chain mapped
- Hallucination guard: MEDIUM - detection patterns need empirical validation
- Memory agent Redis: HIGH - all exception paths verified

**Research date:** 2026-03-18
**Valid until:** 2026-04-18 (stable codebase, no external dependencies changing)

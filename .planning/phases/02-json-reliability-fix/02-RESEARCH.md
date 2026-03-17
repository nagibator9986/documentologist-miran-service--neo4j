# Phase 2: JSON Reliability Fix - Research

**Researched:** 2026-03-17
**Domain:** Ollama JSON mode, Pydantic v2 schema validation, LLM output parsing with retry
**Confidence:** HIGH

## Summary

Phase 2 addresses JSON parse reliability across three agents (verify, generate, analyze) that produce structured output via Ollama's `format="json"` mode. The current implementation uses `get_json_llm()` which sets `format="json"` and `temperature=0.0` on `ChatOllama`, then feeds the raw LLM string through `safe_parse_json()` which does brace-counting extraction with fallback. There is NO Pydantic schema validation, NO retry on parse failure, and NO explicit "no backticks" instruction in any prompt. The prompts show example JSON but don't constrain the model to follow the exact schema.

The fix strategy is clear: (1) define Pydantic v2 BaseModel schemas for each agent's JSON output, (2) create a `parse_with_retry` function that calls the LLM, validates against the schema, and retries on failure with a corrective prompt, (3) pass `model_json_schema()` to Ollama's `format` parameter for constrained generation (instead of plain `format="json"`), and (4) add explicit anti-markdown instructions to all JSON system prompts. This approach uses Ollama's native structured output support (available since Dec 2024) which constrains the model's token generation to only produce schema-valid JSON.

**Primary recommendation:** Use Ollama's native JSON schema format constraint (`format=MyModel.model_json_schema()`) via ChatOllama instead of plain `format="json"`. Combine with Pydantic validation and a 3-retry loop. This eliminates most failure modes (wrong keys, extra fields, truncation) at the generation level rather than relying on post-hoc parsing.

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| pydantic | >=2.0 (already via pydantic-settings) | JSON schema definition + output validation | Already a dependency; model_json_schema() produces Ollama-compatible schemas |
| langchain-ollama | >=0.2.0 (already in requirements) | ChatOllama with format parameter | Already used; format accepts JSON schema dict since Ollama structured output support |
| tenacity | (already in requirements via llm.py) | Retry logic for parse failures | Already used for connection retries; extend to JSON parse retries |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| langchain-core | >=0.2.0 (already) | SystemMessage/HumanMessage types | Already used in all agents |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Ollama format=schema | ChatOllama.with_structured_output() | with_structured_output has reported bugs with nested schemas on ChatOllama (GitHub issues #25343, #29410); direct format=schema is more reliable and gives full control |
| Pydantic validation | JsonOutputParser from langchain | Adds unnecessary abstraction; Pydantic alone is simpler and already a dependency |
| Custom parse_with_retry | instructor library | Adds new dependency; overkill for 4 schemas with straightforward structure |

**Installation:**
No new packages needed. All dependencies are already in requirements.txt.

## Architecture Patterns

### Recommended Project Structure
```
app/
├── core/
│   ├── json_output.py       # NEW: Pydantic schemas + parse_with_retry
│   ├── llm.py               # MODIFY: add get_schema_llm(schema) factory
│   └── utils.py             # KEEP: safe_parse_json stays as fallback
├── prompts/
│   └── __init__.py           # MODIFY: add anti-markdown instructions to JSON prompts
└── agents/
    ├── verify_agent.py       # MODIFY: use parse_with_retry + VerifyResult
    ├── generate_agent.py     # MODIFY: use parse_with_retry + GeneratePlan
    └── analyze_agent.py      # MODIFY: use parse_with_retry + AnalyzeCompareResult/ExtractResult
```

### Pattern 1: Pydantic Schemas for Agent Outputs
**What:** Define strict Pydantic v2 BaseModel classes for each JSON-producing agent.
**When to use:** Every agent that currently uses `get_json_llm()` + `safe_parse_json()`.
**Example:**
```python
# app/core/json_output.py
from pydantic import BaseModel, Field

class VerifyResult(BaseModel):
    compliant: bool = Field(description="True if document/scenario is compliant")
    risk_score: int = Field(ge=0, le=10, description="Risk level 0-10")
    issues: list[str] = Field(default_factory=list, description="Identified violations")
    law_refs: list[str] = Field(default_factory=list, description="Applicable law references")
    fix_hints: list[str] = Field(default_factory=list, description="Remediation suggestions")

class GeneratePlan(BaseModel):
    template_type: str = Field(description="Document type: contract, report, letter, etc.")
    title: str = Field(description="Document title in Russian")
    context: dict = Field(default_factory=dict, description="Document context details")
    export_format: str = Field(default="docx", description="Export format: docx or pdf")
    required_sections: list[str] = Field(default_factory=list, description="Required document sections")

class AnalyzeCompareResult(BaseModel):
    task: str = Field(default="compare")
    similarities: list[str] = Field(default_factory=list)
    differences: list[str] = Field(default_factory=list)
    legal_conflicts: list[str] = Field(default_factory=list)
    recommendation: str = Field(default="")
    confidence: float = Field(default=0.9, ge=0.0, le=1.0)

class AnalyzeExtractResult(BaseModel):
    task: str = Field(default="extract")
    result: str = Field(default="")
    entities: list[dict] = Field(default_factory=list)
    key_points: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.9, ge=0.0, le=1.0)
```

### Pattern 2: Schema-Constrained LLM Factory
**What:** A new `get_schema_llm(schema)` function that passes `model_json_schema()` to Ollama's format parameter.
**When to use:** Instead of `get_json_llm()` when a Pydantic schema is available.
**Example:**
```python
# app/core/llm.py — new function
from pydantic import BaseModel

def get_schema_llm(
    schema: type[BaseModel],
    *,
    num_predict: int | None = None,
) -> ChatOllama:
    """Return a ChatOllama that constrains output to a Pydantic schema."""
    s = get_settings()
    return ChatOllama(
        base_url=s.ollama_url,
        model=s.ollama_model,
        temperature=0.0,
        num_predict=num_predict or 1024,
        format=schema.model_json_schema(),  # Ollama structured output
        timeout=s.ollama_timeout,
    )
```

### Pattern 3: parse_with_retry
**What:** A reusable function that calls the LLM, validates against schema, and retries on failure.
**When to use:** Every JSON-producing agent call.
**Example:**
```python
# app/core/json_output.py
import json
import logging
from typing import TypeVar

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, ValidationError

from .llm import get_schema_llm, invoke_with_retry
from .utils import safe_parse_json

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

def parse_with_retry(
    messages: list[BaseMessage],
    schema: type[T],
    *,
    num_predict: int = 1024,
    max_retries: int = 3,
) -> tuple[T | None, bool]:
    """Call LLM with schema constraint, validate, retry on failure.

    Returns:
        (parsed_result, success) — parsed_result is None on total failure.
    """
    llm = get_schema_llm(schema, num_predict=num_predict)

    for attempt in range(1, max_retries + 1):
        raw = invoke_with_retry(llm, messages)

        if not raw:
            logger.warning("parse_with_retry: empty LLM response (attempt %d/%d)", attempt, max_retries)
            continue

        # Try Pydantic validation first (schema-constrained output should be clean)
        try:
            return schema.model_validate_json(raw), True
        except (ValidationError, json.JSONDecodeError) as exc:
            logger.warning(
                "parse_with_retry: validation failed (attempt %d/%d): %s",
                attempt, max_retries, exc,
            )

        # Fallback: try safe_parse_json + model_validate (handles extra text/fences)
        parsed = safe_parse_json(raw, {})
        if parsed and not parsed.get("_parse_failed"):
            try:
                return schema.model_validate(parsed), True
            except ValidationError:
                pass

        # Add corrective instruction for next retry
        if attempt < max_retries:
            messages = messages + [
                HumanMessage(content=(
                    "Your previous response was not valid JSON matching the required schema. "
                    "Return ONLY raw JSON matching the schema, no markdown, no backticks, no explanation."
                )),
            ]

    return None, False
```

### Pattern 4: Prompt Anti-Markdown Instructions
**What:** Add explicit instructions to all JSON system prompts to prevent markdown wrapping.
**When to use:** Every system prompt for JSON-producing agents.
**Example addition to each JSON prompt:**
```
КРИТИЧЕСКИ ВАЖНО: Верни ТОЛЬКО чистый JSON объект.
НЕ оборачивай в ```json``` или другие markdown-блоки.
НЕ добавляй текст до или после JSON.
Ответ должен начинаться с { и заканчиваться на }.
```

### Anti-Patterns to Avoid
- **Using `with_structured_output()` on ChatOllama:** Has known issues with nested Pydantic models (GitHub issues). Direct `format=schema.model_json_schema()` is more reliable.
- **Removing `safe_parse_json`:** Keep it as a secondary fallback inside `parse_with_retry`. Belt-and-suspenders.
- **Increasing `num_predict` as primary fix:** The real fix is schema-constrained generation, not giving the model more tokens to wander.
- **Separate retry decorator per agent:** Centralize in `parse_with_retry` to avoid code duplication.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| JSON schema generation | Manual JSON schema dicts | `MyModel.model_json_schema()` | Pydantic handles all edge cases (nested types, optionals, defaults) |
| JSON validation | Manual key checking | `schema.model_validate_json(raw)` | Handles type coercion, default values, constraint validation |
| Output format constraint | Prompt engineering alone | Ollama `format=schema` parameter | Token-level constraint is fundamentally more reliable than prompt instructions |
| Retry with backoff | Custom retry loop | Extend existing `invoke_with_retry` pattern | Already battle-tested for connection retries |

**Key insight:** The biggest reliability gain comes from switching `format="json"` (unconstrained JSON) to `format=schema.model_json_schema()` (schema-constrained JSON). This is an Ollama-native feature that constrains token generation at the grammar level, not just via prompt instructions. Combined with Pydantic validation and retry, this should push well past 95%.

## Common Pitfalls

### Pitfall 1: Ollama Version Compatibility
**What goes wrong:** Older Ollama versions don't support `format=<schema>` (only `format="json"`). The API returns an error if the schema format is not recognized.
**Why it happens:** Ollama structured output support was added Dec 2024. Docker-compose may pin an old Ollama image.
**How to avoid:** Check the Ollama version in docker-compose.yml. Must be >= 0.5.0. Add a fallback to `format="json"` if schema mode fails.
**Warning signs:** Error message `invalid format: expected "json" or a JSON schema`.

### Pitfall 2: Pydantic Schema with `dict` Type
**What goes wrong:** `GeneratePlan.context` is `dict` type. Pydantic's `model_json_schema()` generates `{"type": "object"}` without `additionalProperties`, which some Ollama models handle poorly (generating empty `{}`).
**Why it happens:** Unconstrained `dict` in Pydantic schema produces a loose JSON schema that doesn't guide the model well.
**How to avoid:** For `GeneratePlan`, either (a) keep `context` as `dict` but accept that it may need manual enrichment after parsing, or (b) define a more specific `PlanContext` model. Option (a) is simpler given the current usage where `context` is a freeform dict passed to the template engine.
**Warning signs:** `context` field always returns `{}` or minimal content.

### Pitfall 3: Truncated JSON from num_predict Limit
**What goes wrong:** Long verify results (many issues, many law_refs) get truncated mid-JSON because `num_predict=1024` isn't enough tokens.
**Why it happens:** `num_predict` limits total generated tokens. Russian text is token-heavy (1.5-2x vs English). Legal analysis can be verbose.
**How to avoid:** Use `num_predict=2048` for verify and generate agents. The analyze agent already uses `num_predict=2000`. Monitor `_parse_failed` rate per agent to detect systematic truncation.
**Warning signs:** `safe_parse_json` logs "unmatched braces" consistently for one agent type.

### Pitfall 4: Retry Adds to Latency
**What goes wrong:** 3 retries on a local Ollama model add 30-90 seconds per request (each LLM call is 10-30s).
**Why it happens:** Local model inference is slow. Each retry is a full LLM call.
**How to avoid:** Schema-constrained generation (`format=schema`) should make retries rare (< 5% of calls). The retry is a safety net, not the primary mechanism. If retry rate exceeds 10%, investigate prompt/schema issues rather than increasing retries.
**Warning signs:** P95 latency jumps significantly after this change.

### Pitfall 5: Breaking Existing Fallback Behavior
**What goes wrong:** Currently, `_parse_verify_result` returns a fallback dict with `_parse_failed: True` and agents display "analysis unavailable". Switching to `parse_with_retry` must preserve this graceful degradation.
**Why it happens:** New code returns `None` on total failure, but downstream expects a dict with specific keys.
**How to avoid:** When `parse_with_retry` returns `(None, False)`, construct the same fallback dict that exists today. The verify agent already has `_PARSE_FAILED_RESPONSE` and fallback logic in `_parse_verify_result` -- keep that as the final fallback after retry exhaustion.
**Warning signs:** HTTP 500 errors replacing the current "analysis unavailable" messages.

### Pitfall 6: Prompt Language Mismatch in Retry
**What goes wrong:** The corrective retry message is in English but all prompts and model behavior are optimized for Russian.
**Why it happens:** Copy-paste from English examples.
**How to avoid:** Write corrective retry instructions in Russian to match the system prompt language.
**Warning signs:** Model produces English output on retry attempts.

## Code Examples

### Current get_json_llm (existing — to be enhanced)
```python
# app/core/llm.py lines 77-88 — CURRENT
def get_json_llm(*, num_predict: int | None = None) -> ChatOllama:
    s = get_settings()
    llm = ChatOllama(
        base_url=s.ollama_url,
        model=s.ollama_model,
        temperature=0.0,
        num_predict=num_predict or 1024,
        format="json",             # <-- Only constrains to "valid JSON", not specific schema
        timeout=s.ollama_timeout,
    )
    return llm
```

### Current safe_parse_json (existing — keep as fallback)
```python
# app/core/utils.py lines 177-245 — CURRENT
# Handles:
# 1. Direct json.loads (clean output)
# 2. Brace-counting extraction (finds first complete {} in text)
# Does NOT handle:
# - Markdown fences (```json ... ```)  -- brace-counting will find the inner JSON
# - Schema validation (wrong keys, missing fields, wrong types)
# - Retry on failure
```

### Current Prompts — NO anti-markdown instructions
```python
# VERIFY_COMPLIANCE (prompts/__init__.py line 66-86):
# Says "Выдай JSON:" then shows example structure
# Does NOT say "no backticks" or "raw JSON only"

# GENERATE_PLAN (lines 90-109):
# Says "Выдай JSON:" then shows example structure
# Does NOT say "no backticks" or "raw JSON only"

# ANALYZE_COMPARE (lines 135-150):
# Says "Выдай JSON:" then shows example structure
# Does NOT say "no backticks" or "raw JSON only"

# ANALYZE_EXTRACT (lines 163-179):
# Says "Выдай JSON:" then shows example structure
# Does NOT say "no backticks" or "raw JSON only"
```

### Current _run_compliance_llm (verify_agent.py lines 194-231)
```python
# 1. Constructs prompt with doc_content + legal_context
# 2. llm = get_json_llm(num_predict=1024)
# 3. return invoke_with_retry(llm, [SystemMessage, HumanMessage])
# 4. Returns raw string -> fed to _parse_verify_result -> safe_parse_json
# NO schema validation. NO retry on parse failure. NO anti-markdown prompt.
```

### Current _plan_document (generate_agent.py lines 144-157)
```python
# 1. llm_json = get_json_llm(num_predict=1024)
# 2. raw = invoke_with_retry(llm_json, [SystemMessage, HumanMessage])
# 3. return safe_parse_json(raw, fallback_dict)
# NO schema validation. NO retry on parse failure.
```

### Current _run_analysis_llm (analyze_agent.py lines 322-353)
```python
# For compare/extract tasks:
# 1. llm = get_json_llm(num_predict=2000)
# 2. return invoke_with_retry(llm, [SystemMessage, *history, HumanMessage])
# Then in _parse_result: safe_parse_json(raw, fallback)
# NO schema validation. NO retry on parse failure.
```

### How Agents Currently Track json_parse_success
```python
# verify_agent.py line 376:
#   "json_parse_success": not verify_result.get("_parse_failed", False)
# generate_agent.py line 331:
#   "json_parse_success": not plan.get("_parse_failed", False)
# analyze_agent.py lines 558-560:
#   if task in _JSON_TASKS: _json_parse_success = not analyze_result.get("_parse_failed", False)
# ALL agents already track this metric -- just need to make sure parse_with_retry
# sets _parse_failed correctly on the returned dict when it fails.
```

## Key Technical Findings

### 1. get_json_llm Configuration (HIGH confidence)
- Uses `format="json"` -- forces Ollama to produce valid JSON, but NOT schema-constrained
- `temperature=0.0` -- good, deterministic
- `num_predict=1024` default -- may be insufficient for verbose Russian legal text
- Does NOT pass any schema information to Ollama

### 2. safe_parse_json Capabilities (HIGH confidence)
- Handles direct JSON parsing and brace-counting extraction
- Does NOT strip markdown fences explicitly (but brace-counting finds JSON inside fences)
- Does NOT validate schema structure (wrong keys pass through)
- Returns fallback dict with `_parse_failed: True` on failure
- This is a GOOD fallback layer to keep

### 3. Current Prompt Deficiencies (HIGH confidence)
All four JSON prompts (VERIFY_COMPLIANCE, GENERATE_PLAN, ANALYZE_COMPARE, ANALYZE_EXTRACT):
- Show example JSON structure with `"Выдай JSON:"`
- Do NOT include "no backticks" or "no markdown" instruction
- Do NOT specify exact field types or constraints
- Do NOT mention that output must be raw JSON only
- The model may wrap output in ```json fences or add explanatory text

### 4. Ollama Structured Output Support (HIGH confidence)
Since Ollama 0.5.0 (Dec 2024), the `format` parameter accepts a JSON schema dict (not just `"json"` string). This enables grammar-level output constraint where the model can only generate tokens that produce valid JSON matching the schema. This is fundamentally more reliable than prompt-based guidance.

### 5. ChatOllama format Parameter (MEDIUM confidence)
`langchain-ollama`'s `ChatOllama` passes the `format` parameter directly to the Ollama API. Passing a dict (from `model_json_schema()`) should work as Ollama's native schema constraint. However, `with_structured_output()` has reported issues with nested schemas on ChatOllama (GitHub issues #25343, #29410). Direct `format=schema` is recommended over `with_structured_output()`.

### 6. Existing Retry Infrastructure (HIGH confidence)
`invoke_with_retry` in `llm.py` retries on `ConnectionError, TimeoutError, OSError` with exponential backoff. It does NOT retry on JSON parse failure. The `parse_with_retry` function should be a new function that wraps `invoke_with_retry` and adds parse-level retry logic.

### 7. Eval JSON Validity Tests (HIGH confidence)
The eval dataset has 10 json_validity tests (json-01 through json-10):
- 4 verify queries (json-01 to json-04)
- 3 generate queries (json-05 to json-07)
- 3 analyze queries (json-08 to json-10, covering compare and extract)
The eval runner checks `json_valid` by verifying that `verify_result`/`generate_result`/`analyze_result` is a non-empty dict. This is a basic check -- does NOT validate schema structure. After Phase 2, the eval should additionally validate that the result matches the Pydantic schema.

### 8. Failure Modes Analysis (HIGH confidence)
Based on code analysis, the likely failure modes are:
1. **Markdown-wrapped JSON** -- model outputs ```json { ... } ``` -- brace-counting in safe_parse_json handles this, but schema constraint would prevent it entirely
2. **Wrong/extra keys** -- model invents fields or uses English instead of expected Russian keys -- only schema constraint prevents this
3. **Truncated JSON** -- num_predict=1024 runs out mid-generation -- increase num_predict + schema constraint may help
4. **Type mismatches** -- risk_score as string "8" instead of int 8 -- Pydantic validation catches and coerces this
5. **Non-JSON output** -- model ignores format instruction entirely -- rarest with format="json", near-impossible with format=schema

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `format="json"` (unconstrained) | `format=schema.model_json_schema()` (constrained) | Ollama 0.5.0, Dec 2024 | Eliminates wrong-key, extra-field, type-mismatch failures |
| No schema validation | Pydantic v2 model_validate_json | Pydantic v2.0, 2023 | Type coercion + constraint enforcement + clear error messages |
| Single attempt | parse_with_retry (3 attempts) | New pattern | Catches remaining edge cases after schema constraint |
| Prompt says "give JSON" | Prompt + schema constraint + anti-markdown | Combined approach | Defense in depth: grammar constraint + prompt + validation + retry |

**Already implemented (don't redo):**
- `safe_parse_json` with brace-counting -- keep as fallback layer
- `invoke_with_retry` with tenacity -- extend pattern, don't replace
- `_parse_failed` tracking in all agents -- preserve this interface
- `json_parse_success` in `retrieval_metrics` -- already wired up

## Open Questions

1. **What Ollama version is running in docker-compose?**
   - What we know: Structured output needs Ollama >= 0.5.0 (Dec 2024)
   - What's unclear: The docker-compose.yml Ollama image tag
   - Recommendation: Check during implementation. If old, update the image. If updating is not possible, fall back to `format="json"` + prompt + validation + retry (still an improvement over current state).

2. **GeneratePlan.context as dict vs typed model**
   - What we know: The `context` field is currently a freeform dict that gets passed to `doc_generate` tool
   - What's unclear: Whether Ollama handles `{"type": "object"}` well in schema constraint mode
   - Recommendation: Start with `dict` type. If Ollama produces empty/minimal context, create a `PlanContext` Pydantic model with explicit fields (parties, subject, clauses, date, references).

3. **Eval test enhancement**
   - What we know: Current json_validity tests only check for non-empty dict
   - What's unclear: Whether to add schema validation to the eval runner in this phase
   - Recommendation: Add Pydantic schema validation to `run_eval.py` json_validity checks as part of task 7. This makes the eval test meaningful.

## Sources

### Primary (HIGH confidence)
- Direct codebase analysis: `llm.py`, `utils.py`, `prompts/__init__.py`, `verify_agent.py`, `generate_agent.py`, `analyze_agent.py`, `config.py`, `state.py`, `tests/eval/dataset.json`, `tests/eval/run_eval.py`
- [Ollama Structured Outputs Documentation](https://docs.ollama.com/capabilities/structured-outputs) -- format=schema, best practices
- [Ollama Structured Outputs Blog](https://ollama.com/blog/structured-outputs) -- Dec 2024 release, Pydantic examples

### Secondary (MEDIUM confidence)
- [ChatOllama with_structured_output issues - GitHub #25343](https://github.com/langchain-ai/langchain/issues/25343) -- nested schema bugs
- [ChatOllama with_structured_output not honoured - GitHub #29410](https://github.com/langchain-ai/langchain/issues/29410) -- format not respected
- [Conflicting docs on ChatOllama structured output - GitHub #28691](https://github.com/langchain-ai/langchain/issues/28691) -- resolved, support confirmed

### Tertiary (LOW confidence)
- None

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all libraries already in dependencies, no new packages needed
- Architecture: HIGH -- patterns derived from direct code analysis + official Ollama docs
- Pitfalls: HIGH -- identified from actual code review and known GitHub issues
- Ollama schema support: MEDIUM -- documented but not tested against this specific codebase's Ollama version

**Research date:** 2026-03-17
**Valid until:** 2026-04-17 (stable domain, no fast-moving dependencies)

"""Pydantic schemas for structured agent outputs + parse-with-retry layer."""
from __future__ import annotations

import json
import logging
from typing import TypeVar

from langchain_core.messages import BaseMessage, HumanMessage
from pydantic import BaseModel, Field, ValidationError

from .llm import get_schema_llm, invoke_with_retry
from .utils import safe_parse_json

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


# ---------------------------------------------------------------------------
# Pydantic v2 schemas — one per JSON-producing agent
# ---------------------------------------------------------------------------


class VerifyResult(BaseModel):
    """Structured output for verify_agent compliance check."""

    compliant: bool = Field(description="True if document/scenario is compliant")
    risk_score: int = Field(ge=0, le=10, description="Risk level 0-10")
    issues: list[str] = Field(default_factory=list, description="Identified violations")
    law_refs: list[str] = Field(default_factory=list, description="Applicable law references")
    fix_hints: list[str] = Field(default_factory=list, description="Remediation suggestions")


class GeneratePlan(BaseModel):
    """Structured output for generate_agent document planning."""

    template_type: str = Field(description="Document type: contract, report, letter, etc.")
    title: str = Field(description="Document title in Russian")
    context: dict = Field(default_factory=dict, description="Document context details")
    export_format: str = Field(default="docx", description="Export format: docx or pdf")
    required_sections: list[str] = Field(
        default_factory=list, description="Required document sections"
    )


class AnalyzeCompareResult(BaseModel):
    """Structured output for analyze_agent compare task."""

    task: str = Field(default="compare")
    similarities: list[str] = Field(default_factory=list)
    differences: list[str] = Field(default_factory=list)
    legal_conflicts: list[str] = Field(default_factory=list)
    recommendation: str = Field(default="")
    confidence: float = Field(default=0.9, ge=0.0, le=1.0)


class AnalyzeExtractResult(BaseModel):
    """Structured output for analyze_agent extract task."""

    task: str = Field(default="extract")
    result: str = Field(default="")
    entities: list[dict] = Field(default_factory=list)
    key_points: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.9, ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# parse_with_retry — schema-constrained LLM call with validation + retry
# ---------------------------------------------------------------------------

_RETRY_INSTRUCTION_RU = (
    "Твой предыдущий ответ не является валидным JSON, соответствующим требуемой схеме. "
    "Верни ТОЛЬКО чистый JSON объект, без markdown, без обратных кавычек, без пояснений."
)


def parse_with_retry(
    messages: list[BaseMessage],
    schema: type[T],
    *,
    num_predict: int = 1024,
    max_retries: int = 3,
) -> tuple[T | None, bool]:
    """Call LLM with schema constraint, validate with Pydantic, retry on failure.

    Returns:
        (parsed_result, success) -- parsed_result is None on total failure.
    """
    llm = get_schema_llm(schema, num_predict=num_predict)
    current_messages = list(messages)

    for attempt in range(1, max_retries + 1):
        raw = invoke_with_retry(llm, current_messages)

        if not raw:
            logger.warning(
                "parse_with_retry: empty LLM response (attempt %d/%d)",
                attempt,
                max_retries,
            )
            continue

        # Primary: Pydantic validation of raw JSON string
        try:
            return schema.model_validate_json(raw), True
        except (ValidationError, json.JSONDecodeError) as exc:
            logger.warning(
                "parse_with_retry: validation failed (attempt %d/%d): %s",
                attempt,
                max_retries,
                exc,
            )

        # Secondary: safe_parse_json extracts JSON from fences/extra text, then validate
        parsed = safe_parse_json(raw, {})
        if parsed:
            try:
                return schema.model_validate(parsed), True
            except ValidationError:
                pass

        # Add corrective instruction for next retry (in Russian to match prompts)
        if attempt < max_retries:
            current_messages = current_messages + [
                HumanMessage(content=_RETRY_INSTRUCTION_RU),
            ]

    logger.error(
        "parse_with_retry: exhausted %d retries for schema %s",
        max_retries,
        schema.__name__,
    )
    return None, False

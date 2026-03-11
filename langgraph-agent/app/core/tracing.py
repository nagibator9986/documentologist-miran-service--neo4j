"""MLflow observability — single integration point for the entire service.

All MLflow-specific code lives here. Agents, tools, and the graph are
completely unaware of tracing.

Public API
----------
setup_mlflow(settings)      — call once at application startup
trace_node(fn)              — decorator for LangGraph node functions (workflow.py only)
register_all_prompts(settings) — snapshot all prompt versions into MLflow at startup

Design principles
-----------------
- mlflow_enabled=False  → every public function is a no-op (zero import overhead)
- autolog               → automatic LLM call tracing (inputs, outputs, latency)
- trace_node            → child spans per LangGraph node (latency, retrieval metrics)
- register_all_prompts  → prompt version history; diff artifacts to track changes
- Non-fatal everywhere  → MLflow unavailability never crashes the application
"""
from __future__ import annotations

import logging
from functools import wraps
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


# ── Public API ────────────────────────────────────────────────────────────────


def setup_mlflow(settings: Any) -> None:
    """Configure MLflow tracking URI, experiment, and LangChain autolog.

    Must be called once during application lifespan startup, after settings
    are loaded.  Idempotent — safe to call multiple times.

    Args:
        settings: The application Settings instance from core.config.
    """
    if not settings.mlflow_enabled:
        logger.info("MLflow tracing disabled (MLFLOW_ENABLED=false) — skipping setup")
        return

    try:
        import mlflow
        import mlflow.langchain

        mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
        mlflow.set_experiment(settings.mlflow_experiment_name)

        # LangChain autolog: intercepts all ChatOllama / chain invocations
        # and records inputs, outputs, token counts, and latency automatically.
        # log_models=False avoids storing large model artefacts in the registry.
        mlflow.langchain.autolog(
            log_input_examples=True,
            log_model_signatures=False,
            log_models=False,
            silent=False,
            extra_tags={
                "project":    "miran-agent",
                "env":        settings.app_env,
                "llm_model":  settings.ollama_model,
                "embed_model": settings.ollama_embedding_model,
            },
        )

        logger.info(
            "MLflow ready — tracking_uri=%s experiment=%s",
            settings.mlflow_tracking_uri,
            settings.mlflow_experiment_name,
        )

    except ImportError:
        logger.warning(
            "mlflow package not installed — tracing disabled. "
            "Run: pip install mlflow boto3"
        )
    except Exception as exc:
        logger.warning("MLflow setup failed (non-critical, tracing disabled): %s", exc)


def trace_node(fn: F) -> F:
    """Wrap a LangGraph node function with an MLflow child span.

    Creates a span named after the wrapped function.  The span is a child of
    the top-level trace that autolog creates for the whole graph.invoke() call.

    Automatically:
    - Sets session_id / user_id / intent as run tags (once per graph run).
    - Logs all numeric fields from state["retrieval_metrics"] as MLflow metrics.
    - Sets span inputs (user_query, intent) and outputs (elapsed_s, has_context).

    Usage — ONLY in workflow.py, never inside agent files:
        builder.add_node("search", trace_node(search_node))

    If MLflow is disabled or unavailable the original function is called
    without any overhead.
    """
    @wraps(fn)
    def wrapper(state: dict) -> dict:
        from .config import get_settings
        s = get_settings()

        if not s.mlflow_enabled:
            return fn(state)

        try:
            import mlflow
            from mlflow.entities import SpanType

            with mlflow.start_span(
                name=fn.__name__,
                span_type=SpanType.AGENT,
            ) as span:
                span.set_inputs({
                    "user_query": (state.get("user_query") or "")[:300],
                    "intent":     state.get("intent", ""),
                    "session_id": state.get("session_id", ""),
                })

                # Enrich the active MLflow run with session context.
                # set_tags is idempotent — harmless to call on every node.
                _tag_active_run(state)

                result = fn(state)

                # Retrieval metrics are produced by search / verify / generate /
                # analyze nodes.  Log all numeric fields automatically.
                metrics = (result or {}).get("retrieval_metrics") or {}
                if metrics:
                    _log_numeric_metrics(metrics)

                span.set_outputs({
                    "intent":      (result or {}).get("intent", ""),
                    "elapsed_s":   metrics.get("elapsed_s"),
                    "has_context": int(metrics.get("has_context", False))
                    if isinstance(metrics.get("has_context"), bool)
                    else metrics.get("has_context"),
                })

                return result

        except Exception as exc:
            # MLflow errors must never propagate to the user — log and fall through.
            logger.debug(
                "trace_node(%s) failed (non-critical, falling through): %s",
                fn.__name__, exc,
            )
            return fn(state)

    return wrapper  # type: ignore[return-value]


def register_all_prompts(settings: Any) -> None:
    """Snapshot all system prompt versions as MLflow artefacts.

    Creates a dedicated run tagged ``type=prompt_registry`` in the experiment.
    Each application startup produces a new run — diff the artefacts in the
    MLflow UI to see what changed between deployments.

    Non-fatal: if MLflow is unreachable the application starts normally.

    Args:
        settings: The application Settings instance from core.config.
    """
    if not settings.mlflow_enabled:
        return

    try:
        import mlflow

        from ..prompts import (
            ANALYZE_COMPARE,
            ANALYZE_DOCUMENT,
            GENERATE_PLAN,
            GENERATE_VALIDATE,
            SEARCH_EXPERT,
            SEARCH_EXPAND_QUERY,
            SUPERVISOR_CLASSIFY,
            VERIFY_COMPLIANCE,
        )

        _PROMPTS: dict[str, str] = {
            "supervisor_classify":  SUPERVISOR_CLASSIFY,
            "search_expert":        SEARCH_EXPERT,
            "search_expand_query":  SEARCH_EXPAND_QUERY,
            "verify_compliance":    VERIFY_COMPLIANCE,
            "generate_plan":        GENERATE_PLAN,
            "generate_validate":    GENERATE_VALIDATE,
            "analyze_document":     ANALYZE_DOCUMENT,
            "analyze_compare":      ANALYZE_COMPARE,
        }

        with mlflow.start_run(
            run_name="prompt_registry",
            tags={
                "type":         "prompt_registry",
                "env":          settings.app_env,
                "prompt_count": str(len(_PROMPTS)),
                "llm_model":    settings.ollama_model,
            },
        ):
            for name, content in _PROMPTS.items():
                mlflow.log_text(content, f"prompts/{name}.txt")

            mlflow.log_params({
                "prompt_count": len(_PROMPTS),
                "llm_model":    settings.ollama_model,
            })

        logger.info(
            "MLflow: registered %d prompts in experiment=%s",
            len(_PROMPTS), settings.mlflow_experiment_name,
        )

    except ImportError:
        logger.warning("mlflow not installed — prompt registration skipped")
    except Exception as exc:
        logger.warning("MLflow prompt registration failed (non-critical): %s", exc)


# ── Private helpers ───────────────────────────────────────────────────────────


def _tag_active_run(state: dict) -> None:
    """Attach session / user / intent tags to the currently active MLflow run.

    Called inside trace_node on every node execution.  MLflow set_tags is
    idempotent, so repeated calls with the same values are harmless.
    Tags are updated as intent becomes known (after supervisor node).
    """
    try:
        import mlflow

        active = mlflow.active_run()
        if not active:
            return

        tags: dict[str, str] = {}
        if session_id := state.get("session_id"):
            tags["session_id"] = str(session_id)
        if user_id := state.get("user_id"):
            tags["user_id"] = str(user_id)
        if intent := state.get("intent"):
            tags["intent"] = str(intent)
        if intents := state.get("intents"):
            tags["intents"] = ",".join(intents)

        if tags:
            mlflow.set_tags(tags)

    except Exception:
        pass  # tag enrichment is best-effort


def _log_numeric_metrics(metrics: dict) -> None:
    """Log numeric and boolean fields from a retrieval_metrics dict.

    Booleans are converted to 0/1 so MLflow can chart them over time.
    Non-numeric fields (strings, lists) are silently ignored.
    """
    try:
        import mlflow

        to_log: dict[str, float] = {}
        for k, v in metrics.items():
            if isinstance(v, bool):
                to_log[k] = float(v)
            elif isinstance(v, (int, float)):
                to_log[k] = float(v)
            # str / list / None fields are intentionally skipped

        if to_log:
            mlflow.log_metrics(to_log)

    except Exception:
        pass  # metric logging is best-effort

#!/usr/bin/env python3
"""Eval runner — execute dataset queries against the agent API and compute baseline metrics.

Usage:
    python tests/eval/run_eval.py --base-url http://localhost:8001/api/v1 --timeout 120
    python tests/eval/run_eval.py --category routing

Output:
    - Formatted summary table to stdout
    - Detailed per-query results to tests/eval/results.json (gitignored)
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# Dataset loading
# ---------------------------------------------------------------------------

DATASET_PATH = Path(__file__).parent / "dataset.json"


def load_dataset(path: Path = DATASET_PATH, category: str | None = None) -> list[dict]:
    """Load eval dataset from JSON file, optionally filtering by category."""
    with open(path, encoding="utf-8") as f:
        entries = json.load(f)
    if category:
        entries = [e for e in entries if e.get("category") == category]
    return entries


# ---------------------------------------------------------------------------
# Query execution
# ---------------------------------------------------------------------------


def run_query(
    client: httpx.Client,
    entry: dict,
    base_url: str,
    timeout: int,
) -> dict[str, Any]:
    """Execute a single eval query and return the result record."""
    query_id = entry["id"]
    payload = {
        "query": entry["query"],
        "session_id": f"eval-{query_id}",
    }

    t0 = time.perf_counter()
    try:
        resp = client.post(
            f"{base_url}/chat/",
            json=payload,
            timeout=timeout,
        )
        elapsed = time.perf_counter() - t0
        status = resp.status_code
        body = resp.json() if status == 200 else {}
    except httpx.TimeoutException:
        elapsed = time.perf_counter() - t0
        return {
            "id": query_id,
            "status": 0,
            "error": "timeout",
            "elapsed_s": round(elapsed, 2),
            "routing_correct": None,
            "json_valid": None,
            "retrieval_hit": None,
        }
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        return {
            "id": query_id,
            "status": 0,
            "error": str(exc),
            "elapsed_s": round(elapsed, 2),
            "routing_correct": None,
            "json_valid": None,
            "retrieval_hit": None,
        }

    # --- Evaluate routing ---
    response_intent = body.get("intent", "")
    expected_intent = entry.get("expected_intent", "")
    routing_correct = response_intent == expected_intent if expected_intent else None

    # --- Evaluate JSON validity ---
    json_valid: bool | None = None
    if entry.get("expected_json_valid") is not None:
        if expected_intent == "verify":
            vr = body.get("verify_result")
            json_valid = isinstance(vr, dict) and vr is not None and len(vr) > 0
        elif expected_intent == "generate":
            gr = body.get("generate_result")
            json_valid = isinstance(gr, dict) and gr is not None and len(gr) > 0
        elif expected_intent == "analyze":
            ar = body.get("analyze_result")
            json_valid = isinstance(ar, dict) and ar is not None and len(ar) > 0
        else:
            json_valid = None

    # --- Evaluate retrieval ---
    retrieval_hit: bool | None = None
    expected_docs = entry.get("expected_docs") or []
    if expected_docs:
        citations = body.get("citations") or []
        found_filenames: set[str] = set()
        for cit in citations:
            fn = cit.get("filename") or cit.get("source") or ""
            if fn:
                found_filenames.add(fn.lower())
        hits = sum(
            1 for doc in expected_docs
            if doc.lower() in found_filenames
        )
        retrieval_hit = hits > 0

    return {
        "id": query_id,
        "status": status,
        "error": None,
        "elapsed_s": round(elapsed, 2),
        "response_intent": response_intent,
        "expected_intent": expected_intent,
        "routing_correct": routing_correct,
        "json_valid": json_valid,
        "retrieval_hit": retrieval_hit,
        "category": entry.get("category", ""),
    }


# ---------------------------------------------------------------------------
# Metrics computation
# ---------------------------------------------------------------------------


def compute_metrics(results: list[dict]) -> dict[str, Any]:
    """Compute aggregate metrics from individual query results."""
    total = len(results)
    errors = sum(1 for r in results if r.get("status", 0) != 200)

    # Routing accuracy
    routing_results = [r for r in results if r.get("routing_correct") is not None]
    routing_correct = sum(1 for r in routing_results if r["routing_correct"])
    routing_total = len(routing_results)
    routing_accuracy = (routing_correct / routing_total) if routing_total > 0 else 0.0

    # JSON validity rate
    json_results = [r for r in results if r.get("json_valid") is not None]
    json_valid = sum(1 for r in json_results if r["json_valid"])
    json_total = len(json_results)
    json_validity_rate = (json_valid / json_total) if json_total > 0 else 0.0

    # Retrieval recall@5
    retrieval_results = [r for r in results if r.get("retrieval_hit") is not None]
    retrieval_hits = sum(1 for r in retrieval_results if r["retrieval_hit"])
    retrieval_total = len(retrieval_results)
    retrieval_recall = (retrieval_hits / retrieval_total) if retrieval_total > 0 else 0.0

    # Latency
    elapsed_values = [r["elapsed_s"] for r in results if r.get("elapsed_s") is not None]
    avg_elapsed = statistics.mean(elapsed_values) if elapsed_values else 0.0
    p95_elapsed = _percentile(elapsed_values, 95) if elapsed_values else 0.0

    return {
        "total_queries": total,
        "errors": errors,
        "routing_accuracy": round(routing_accuracy, 4),
        "routing_correct": routing_correct,
        "routing_total": routing_total,
        "json_validity_rate": round(json_validity_rate, 4),
        "json_valid": json_valid,
        "json_total": json_total,
        "retrieval_recall_at_5": round(retrieval_recall, 4),
        "retrieval_hits": retrieval_hits,
        "retrieval_total": retrieval_total,
        "avg_elapsed_s": round(avg_elapsed, 1),
        "p95_elapsed_s": round(p95_elapsed, 1),
    }


def _percentile(data: list[float], pct: float) -> float:
    """Compute the p-th percentile of a list of floats."""
    if not data:
        return 0.0
    sorted_data = sorted(data)
    k = (pct / 100.0) * (len(sorted_data) - 1)
    f = int(k)
    c = f + 1
    if c >= len(sorted_data):
        return sorted_data[-1]
    return sorted_data[f] + (k - f) * (sorted_data[c] - sorted_data[f])


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


def print_summary(metrics: dict[str, Any]) -> None:
    """Print formatted summary table to stdout."""
    ra = metrics["routing_accuracy"] * 100
    jv = metrics["json_validity_rate"] * 100
    rr = metrics["retrieval_recall_at_5"] * 100

    print("\n=== Eval Baseline Results ===")
    print(f"Total queries:        {metrics['total_queries']}")
    print(f"Errors (non-200):     {metrics['errors']}")
    print(
        f"Routing accuracy:     {ra:.1f}% "
        f"({metrics['routing_correct']}/{metrics['routing_total']})"
    )
    print(
        f"JSON validity rate:   {jv:.1f}% "
        f"({metrics['json_valid']}/{metrics['json_total']})"
    )
    print(
        f"Retrieval recall@5:   {rr:.1f}% "
        f"({metrics['retrieval_hits']}/{metrics['retrieval_total']})"
    )
    print(f"Avg latency:          {metrics['avg_elapsed_s']:.1f}s")
    print(f"P95 latency:          {metrics['p95_elapsed_s']:.1f}s")
    print("=============================\n")


def print_failures(results: list[dict]) -> None:
    """Print per-query failures for debugging."""
    failures: list[str] = []
    for r in results:
        if r.get("error"):
            failures.append(f"- {r['id']}: {r['error']}")
        elif r.get("routing_correct") is False:
            failures.append(
                f"- {r['id']}: expected={r.get('expected_intent')} "
                f"got={r.get('response_intent')} (routing)"
            )
        elif r.get("json_valid") is False:
            failures.append(f"- {r['id']}: structured result is None/empty (json_validity)")
        elif r.get("retrieval_hit") is False:
            failures.append(f"- {r['id']}: expected docs not found in citations (retrieval)")

    if failures:
        print("FAILURES:")
        for line in failures:
            print(line)
        print()


def save_results(results: list[dict], metrics: dict[str, Any]) -> None:
    """Save detailed results to tests/eval/results.json."""
    output_path = Path(__file__).parent / "results.json"
    payload = {
        "metrics": metrics,
        "results": results,
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"Detailed results saved to {output_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    """Run the eval suite and return exit code."""
    parser = argparse.ArgumentParser(description="Run eval suite against agent API")
    parser.add_argument(
        "--base-url",
        default="http://localhost:8001/api/v1",
        help="API base URL (default: http://localhost:8001/api/v1)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="Per-query timeout in seconds (default: 120)",
    )
    parser.add_argument(
        "--category",
        default=None,
        help="Run only this category (routing/json_validity/retrieval)",
    )
    args = parser.parse_args()

    # Load dataset
    dataset = load_dataset(category=args.category)
    if not dataset:
        print(f"No entries found for category={args.category!r}")
        return 0

    print(f"Running {len(dataset)} eval queries against {args.base_url}")
    print(f"Timeout: {args.timeout}s per query")
    if args.category:
        print(f"Category filter: {args.category}")
    print()

    # Execute queries
    results: list[dict] = []
    with httpx.Client() as client:
        for i, entry in enumerate(dataset, 1):
            print(f"  [{i}/{len(dataset)}] {entry['id']}: {entry['query'][:60]}...", end="", flush=True)
            result = run_query(client, entry, args.base_url, args.timeout)
            results.append(result)
            status_icon = "OK" if result.get("status") == 200 else "ERR"
            print(f" {status_icon} ({result['elapsed_s']}s)")

    # Compute and display metrics
    metrics = compute_metrics(results)
    print_summary(metrics)
    print_failures(results)
    save_results(results, metrics)

    return 0


if __name__ == "__main__":
    sys.exit(main())

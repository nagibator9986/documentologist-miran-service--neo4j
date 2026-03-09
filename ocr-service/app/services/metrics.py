"""
Prometheus metrics definitions for Documentolog API.

All metric objects are module-level singletons — import them wherever needed.
The /metrics endpoint in main.py calls generate_latest() to expose them.
"""

from prometheus_client import Counter, Gauge, Histogram

# ── HTTP metrics (populated by PrometheusMiddleware in main.py) ────────────

http_requests_total = Counter(
    "documentolog_http_requests_total",
    "Total number of HTTP requests",
    ["method", "path", "status_code"],
)

http_request_duration_seconds = Histogram(
    "documentolog_http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "path"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

# ── Business metrics ───────────────────────────────────────────────────────

uploads_total = Counter(
    "documentolog_uploads_total",
    "Total document upload attempts",
    ["is_duplicate"],  # "true" | "false"
)

documents_by_status = Gauge(
    "documentolog_documents_by_status",
    "Current number of documents per status (refreshed on every /metrics scrape)",
    ["status"],
)

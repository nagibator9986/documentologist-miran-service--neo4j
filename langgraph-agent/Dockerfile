FROM python:3.11-slim

WORKDIR /service

# System deps: gcc for psycopg2, libpq-dev for PostgreSQL, curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Pre-download cross-encoder model at build time ────────────────────────────
# This avoids a 350 MB download on first request and makes the container
# work in air-gapped / restricted-network environments.
RUN python - <<'EOF'
from sentence_transformers import CrossEncoder
import logging
logging.basicConfig(level=logging.INFO)
primary   = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
fallback  = "cross-encoder/ms-marco-MiniLM-L6-v2"
for model_name in (primary, fallback):
    try:
        CrossEncoder(model_name, max_length=512)
        print(f"Downloaded: {model_name}")
        break
    except Exception as e:
        print(f"Failed {model_name}: {e}")
EOF

COPY . .

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8001 \
    APP_ENV=prod \
    LOG_LEVEL=INFO \
    # HuggingFace cache stays inside the image layer
    HF_HOME=/root/.cache/huggingface \
    TRANSFORMERS_CACHE=/root/.cache/huggingface

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8001/health || exit 1

CMD ["python", "-m", "app.main"]

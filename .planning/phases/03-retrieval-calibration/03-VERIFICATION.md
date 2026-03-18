# Phase 3 Retrieval Calibration - Eval Verification Commands

Run these commands when the API server is up to verify Phase 3 outcomes.

## Prerequisites

Start the API server:
```bash
cd documentologist-miran-service--neo4j/langgraph-agent
# Start server (method depends on deployment -- docker-compose or uvicorn)
```

## Verification Commands

### 1. Full eval suite (all 41 queries)
```bash
cd documentologist-miran-service--neo4j/langgraph-agent
python tests/eval/run_eval.py --timeout 120
```

### 2. Retrieval-only eval (18 queries: 8 baseline + 10 probes)
```bash
cd documentologist-miran-service--neo4j/langgraph-agent
python tests/eval/run_eval.py --category retrieval --timeout 60
```

### 3. Expected results
- `retrieval_recall_at_5 >= 0.80` (phase gate)
- `routing_accuracy` -- no regression from Phase 1 baseline
- `json_validity_rate` -- no regression from Phase 2 baseline
- No Python exceptions or crashes

### 4. If retrieval_recall_at_5 < 0.80
Check `tests/eval/results.json` for per-entry results. Common issues:
- expected_docs filenames don't match actual indexed document names
- Documents not yet indexed in the vector store
- Threshold too aggressive (but 0.25/0.15 should be generous)

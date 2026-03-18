PYTHON ?= .venv/bin/python
PIP    ?= .venv/bin/pip
UV     ?= uv

.PHONY: install venv infra-up infra-down dev build run lint test eval docker-prod-up docker-prod-down

venv:
	python3 -m venv .venv

install: venv
	$(PIP) install -r requirements.txt

infra-up:
	docker compose up -d qdrant neo4j redis postgres minio

infra-down:
	docker compose down

dev:
	$(PYTHON) -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8001

run:
	$(PYTHON) -m app.main

build:
	docker compose build langgraph-agent

up:
	docker compose up -d

down:
	docker compose down

test:
	$(PYTHON) -m pytest tests/ -v

lint:
	$(PYTHON) -m ruff check app/

eval:
	$(PYTHON) tests/eval/run_eval.py

docker-prod-up:
	docker compose -f docker-compose.yml -f docker-compose.prod.yml --compatibility up -d

docker-prod-down:
	docker compose -f docker-compose.yml -f docker-compose.prod.yml down

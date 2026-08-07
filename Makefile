SHELL := /bin/bash

.PHONY: help infra up down logs build dev backend frontend test test-watch lint typecheck prisma-gen proto-gen sandbox-image db-migrate

help:
	@echo "auto-sre-workspace — Make targets"
	@echo "  make up / make dev   docker compose up --build  (run the whole stack)"
	@echo "  make down            docker compose down"
	@echo "  make logs            docker compose logs -f --tail=200"
	@echo "  make sandbox-image   Build the isolated agent sandbox Docker image"
	@echo "  make prisma-gen      Generate Prisma Python client"
	@echo "  make proto-gen       Generate gRPC stubs from proto/runtime.proto"
	@echo "  make test            Run backend pytest suite"
	@echo "  make test-watch      Run backend tests in watch mode"
	@echo "  make lint            Lint backend (ruff) + frontend (eslint)"
	@echo "  make typecheck       Typecheck backend (mypy) + frontend (tsc)"
	@echo "  make db-migrate      Apply Prisma schema migrations"

VENV := .venv
PY := $(VENV)/bin/python

$(VENV):
	python3.11 -m venv $(VENV) || python3 -m venv $(VENV)
	$(VENV)/bin/pip install -U pip setuptools wheel

infra:
	docker compose up -d redis postgres

up:
	docker compose up --build

dev: up

down:
	docker compose down

logs:
	docker compose logs -f --tail=200

sandbox-image:
	docker build -t auto-sre/sandbox:dev -f sandbox/Dockerfile sandbox/

prisma-gen:
	cd backend && prisma generate --schema=db/prisma/schema.prisma

proto-gen:
	cd backend && python -m grpc_tools.protoc -I proto --python_out=generated/proto --pyi_out=generated/proto --grpc_python_out=generated/proto proto/runtime.proto

db-migrate:
	cd backend && prisma migrate dev --schema=db/prisma/schema.prisma

backend:
	. $(VENV)/bin/activate && uvicorn app.main:app --reload --port 8000

frontend:
	cd frontend && npm run dev

test:
	cd backend && . ../$(VENV)/bin/activate && pytest -q

test-watch:
	cd backend && . ../$(VENV)/bin/activate && ptw -- -q

lint:
	cd backend && . ../$(VENV)/bin/activate && ruff check app tests proto
	cd frontend && npx eslint src --ext .ts,.tsx

typecheck:
	cd backend && . ../$(VENV)/bin/activate && mypy app
	cd frontend && npx tsc --noEmit

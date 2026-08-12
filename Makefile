SHELL := /bin/bash
GOFLAGS ?=

.PHONY: help infra up dev down logs build gateway worker ai proxy inference sandbox-image test test-ai test-rust test-go lint typecheck k6 chaos contract db-migrate local-llm llm-routing gpu-llm mobile desktop

help:
	@echo "auto-sre — Make targets (Rust + Go + Python AI gateway)"
	@echo "  make infra             docker compose up -d nats valkey qdrant postgres"
	@echo "  make up / make dev     docker compose up --build (full stack)"
	@echo "  make down              docker compose down"
	@echo "  make logs              docker compose logs -f --tail=200"
	@echo "  make gateway           run the Go gateway locally (go run)"
	@echo "  make ai                run the AI gateway locally (uv run uvicorn)"
	@echo "  make proxy             run the Pingora proxy locally (cargo run)"
	@echo "  make worker            run the Rust worker locally (cargo run)"
	@echo "  make local-llm         docker compose --profile local-llm up -d inference"
	@echo "  make llm-routing       docker compose --profile llm-routing up -d litellm"
	@echo "  make gpu-llm           docker compose --profile gpu-llm up -d vllm ray"
	@echo "  make sandbox-image     build the isolated agent sandbox Docker image"
	@echo "  make test              Python + Go + Rust tests"
	@echo "  make test-ai           cd ai && uv run pytest && uv run behave"
	@echo "  make test-go           go test ./..."
	@echo "  make test-rust         cargo test (worker + inference + proxy)"
	@echo "  make lint              ruff + gofmt/vet + cargo clippy"
	@echo "  make k6                k6 load test (needs infra + gateway up)"
	@echo "  make chaos             chaos/contract soak (needs infra + gateway up)"
	@echo "  make contract          contract tests (go + k6)"
	@echo "  make mobile            scaffold the React Native app (npx expo)"
	@echo "  make desktop           scaffold the Wails desktop app (wails dev)"

infra:
	docker compose up -d nats valkey qdrant postgres

up:
	docker compose up --build

dev: up

down:
	docker compose down

logs:
	docker compose logs -f --tail=200

build:
	docker compose build

gateway:
	cd backend && go run ./cmd/gateway

ai:
	cd ai && uv run uvicorn ai.main:app --port 8002

proxy:
	cd backend/proxy && cargo run

worker:
	cd backend/worker && cargo run

local-llm:
	docker compose --profile local-llm up -d inference

llm-routing:
	docker compose --profile llm-routing up -d litellm

gpu-llm:
	docker compose --profile gpu-llm up -d vllm ray

mobile:
	cd mobile && npx expo start

desktop:
	cd desktop && wails dev

sandbox-image:
	docker build -t auto-sre/sandbox:dev -f sandbox/Dockerfile sandbox/

test: test-ai test-go test-rust

test-ai:
	cd ai && uv run pytest -q && PYTHONPATH=src uv run behave

test-go:
	cd backend && go test ./... -race

test-rust:
	cd backend/worker && cargo test
	cd backend/inference && cargo test
	cd backend/proxy && cargo test

lint:
	cd ai && uv run ruff check src tests
	cd backend && gofmt -l . && go vet ./...
	cd backend/worker && cargo clippy --all-targets
	cd backend/inference && cargo clippy --all-targets
	cd backend/proxy && cargo clippy --all-targets

typecheck: lint

k6:
	k6 run tests/k6/load.js

load: k6

chaos:
	bash tests/chaos/soak.sh

contract:
	cd backend && DATABASE_URL_TEST=$${DATABASE_URL_TEST:-postgresql://sre:sre@localhost:5432/sre} NATS_URL_TEST=$${NATS_URL_TEST:-nats://localhost:4222} go test ./... -run Contract -count=1
	k6 run tests/k6/contract.js

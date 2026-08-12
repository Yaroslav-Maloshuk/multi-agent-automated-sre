SHELL := /bin/bash
GOFLAGS ?=

.PHONY: help infra up dev down logs build gateway worker inference sandbox-image test test-rust test-go lint typecheck k6 chaos contract db-migrate local-llm

help:
	@echo "auto-sre — Make targets (Rust workers + Go gateway, no Python)"
	@echo "  make infra             docker compose up -d nats valkey qdrant postgres"
	@echo "  make up / make dev     docker compose up --build (full stack)"
	@echo "  make down              docker compose down"
	@echo "  make logs              docker compose logs -f --tail=200"
	@echo "  make gateway           run the Go gateway locally (go run)"
	@echo "  make worker            run the Rust worker locally (cargo run)"
	@echo "  make local-llm         docker compose --profile local-llm up -d inference"
	@echo "  make sandbox-image     build the isolated agent sandbox Docker image"
	@echo "  make test              Go tests + Rust tests"
	@echo "  make test-go           go test ./..."
	@echo "  make test-rust         cargo test (worker + inference)"
	@echo "  make lint              gofmt + go vet + cargo clippy"
	@echo "  make k6                k6 load test (needs infra + gateway up)"
	@echo "  make chaos             chaos/contract soak (needs infra + gateway up)"
	@echo "  make contract          contract tests (go + k6)"

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

worker:
	cd backend/worker && cargo run

local-llm:
	docker compose --profile local-llm up -d inference

sandbox-image:
	docker build -t auto-sre/sandbox:dev -f sandbox/Dockerfile sandbox/

test: test-go test-rust

test-go:
	cd backend && go test ./...

test-rust:
	cd backend/worker && cargo test
	cd backend/inference && cargo test

lint:
	cd backend && gofmt -l . && go vet ./...
	cd backend/worker && cargo clippy --all-targets
	cd backend/inference && cargo clippy --all-targets

typecheck: lint

k6:
	k6 run tests/k6/load.js

load: k6

chaos:
	bash tests/chaos/soak.sh

contract:
	cd backend && DATABASE_URL_TEST=$${DATABASE_URL_TEST:-postgresql://sre:sre@localhost:5432/sre} NATS_URL_TEST=$${NATS_URL_TEST:-nats://localhost:4222} go test ./... -run Contract -count=1
	k6 run tests/k6/contract.js

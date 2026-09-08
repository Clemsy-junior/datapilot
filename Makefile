# DataPilot — commandes courantes.
#
# Tout passe par le virtualenv local `.venv` côté Python et par `frontend/node_modules`
# côté Node, y compris dans le dev container : une seule façon de lancer les choses,
# quelle que soit la machine.

SHELL := /bin/bash
.DEFAULT_GOAL := help

VENV        := .venv
PY          := $(VENV)/bin/python
PIP         := $(VENV)/bin/pip
RUFF        := $(VENV)/bin/ruff
PYTEST      := $(VENV)/bin/python -m pytest
NPM         := npm --prefix frontend
COMPOSE     := docker compose
COMPOSE_E2E := docker compose -f docker-compose.yml -f docker-compose.test.yml

.PHONY: help install data dev dev-backend dev-frontend test test-backend test-frontend \
        lint lint-backend lint-frontend format typecheck build up down logs e2e clean

help: ## Affiche cette aide
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install: ## Installe les dépendances backend et frontend
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e "backend[dev]"
	$(NPM) install

data: ## Régénère backend/data/sales.csv (graine fixe, résultat reproductible)
	$(PY) scripts/generate_dataset.py

dev: ## Lance l'API et le front en parallèle (Ctrl-C arrête les deux)
	@echo "API   → http://localhost:8000/api/docs"
	@echo "Front → http://localhost:5173"
	@trap 'kill 0' INT TERM; \
	( cd backend && ../$(VENV)/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 ) & \
	$(NPM) run dev & \
	wait

dev-backend: ## Lance uniquement l'API, avec rechargement à chaud
	cd backend && ../$(VENV)/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

dev-frontend: ## Lance uniquement le front Vite
	$(NPM) run dev

test: test-backend test-frontend ## Lance toute la suite de tests

test-backend: ## pytest avec couverture (seuil 75 %)
	$(PYTEST) backend

test-frontend: ## vitest
	$(NPM) run test

# TODO(R13): rien n'empêche de committer du code non formaté — des hooks
# pre-commit lanceraient ces cibles automatiquement.
lint: lint-backend lint-frontend ## Lint backend et frontend

lint-backend: ## ruff check + ruff format --check
	$(RUFF) check .
	$(RUFF) format --check .

lint-frontend: ## eslint + tsc --noEmit
	$(NPM) run lint
	$(NPM) run typecheck

format: ## Reformate le code Python
	$(RUFF) check --fix .
	$(RUFF) format .

typecheck: ## Vérifie les types TypeScript
	$(NPM) run typecheck

build: ## Construit les images Docker
	$(COMPOSE) build

up: ## Démarre la stack (front sur http://localhost:8080)
	$(COMPOSE) up -d --build
	@echo "Front → http://localhost:8080"
	@echo "API   → http://localhost:8080/api/health (à travers nginx)"

down: ## Arrête la stack et supprime les volumes
	$(COMPOSE) down -v

logs: ## Suit les logs de la stack
	$(COMPOSE) logs -f

e2e: ## Tests d'intégration réels : navigateur → nginx → backend
	$(COMPOSE_E2E) up --build --abort-on-container-exit --exit-code-from integration-tests; \
	status=$$?; \
	$(COMPOSE_E2E) down -v; \
	exit $$status

clean: ## Supprime les artefacts locaux
	rm -rf $(VENV) frontend/node_modules frontend/dist .ruff_cache .pytest_cache .coverage
	find . -name __pycache__ -type d -prune -exec rm -rf {} +

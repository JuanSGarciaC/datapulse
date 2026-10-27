# Comandos para Linux/macOS/CI. En Windows usa scripts/dev.ps1 (mismos nombres).
.PHONY: help setup fmt lint typecheck test test-unit test-live security check migrate generate mock up down logs

help:            ## Lista los comandos
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-12s %s\n", $$1, $$2}'

setup:           ## Instala dependencias (incluye desarrollo)
	uv sync --frozen

fmt:             ## Formatea y aplica correcciones automáticas
	uv run ruff format . && uv run ruff check . --fix

lint:            ## Formato + lint
	uv run ruff format --check . && uv run ruff check .

typecheck:       ## mypy estricto
	uv run mypy

test:            ## Todas las pruebas (integración necesita PostgreSQL)
	uv run pytest --cov

test-unit:       ## Solo pruebas unitarias (sin base de datos)
	uv run pytest tests/unit

test-live:       ## Contrato contra DummyJSON real (requiere internet)
	uv run pytest tests/contract -m live -v

security:        ## bandit + pip-audit
	uv run bandit -q -c pyproject.toml -r src
	uv export --frozen --no-dev --no-hashes --no-emit-project --format requirements-txt > .audit-req.txt
	uv run pip-audit -r .audit-req.txt --progress-spinner off; rc=$$?; rm -f .audit-req.txt; exit $$rc

check: lint typecheck test security  ## Todo lo que corre CI (salvo test-live)
	uv run python -m datapulse.synthetic --check

migrate:         ## Aplica migraciones con las variables de .env
	uv run alembic upgrade head && uv run python -m datapulse.db.bootstrap

generate:        ## Regenera data/samples (determinista, seed 42)
	uv run python -m datapulse.synthetic

mock:            ## API simulada en http://127.0.0.1:8081 (docs en /docs)
	uv run python -m datapulse.mock_source

up:              ## Levanta el entorno Docker
	docker compose up --build -d

down:            ## Detiene el entorno (conserva datos)
	docker compose down

logs:
	docker compose logs -f

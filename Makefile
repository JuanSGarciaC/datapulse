# Comandos para Linux/macOS/CI. En Windows usa scripts/dev.ps1 (mismos nombres).

.PHONY: api dashboard pipeline ingest-samples dbt-build dbt-docs
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

api:             ## API de indicadores en http://127.0.0.1:8000 (usuario de solo lectura)
	set -a; . ./.env; set +a; DATAPULSE_DB_USER=datapulse_api DATAPULSE_DB_PASSWORD="$$DATAPULSE_API_DB_PASSWORD" uv run python -m datapulse.api

dashboard:       ## Dashboard en http://127.0.0.1:8501 (necesita la API)
	uv run python -m datapulse.dashboard

pipeline:        ## Copia data/samples a data/landing y ejecuta el flujo diario
	uv run python -m datapulse.flows seed-landing && uv run python -m datapulse.flows run daily

ingest-samples:  ## Carga data/samples (requiere postgres y mock-source levantados)
	uv run python -m datapulse.ingestion customers data/samples/customers/*.csv
	uv run python -m datapulse.ingestion orders data/samples/orders/*.json
	uv run python -m datapulse.ingestion products

dbt-build:       ## dbt build (modelos + pruebas) como datapulse_dbt
	uv run dbt build --project-dir dbt --profiles-dir dbt

dbt-docs:        ## Genera y sirve la documentación y el linaje de dbt
	uv run dbt docs generate --project-dir dbt --profiles-dir dbt
	uv run dbt docs serve --project-dir dbt --profiles-dir dbt --port 8088

up:              ## Levanta el entorno Docker
	docker compose up --build -d

down:            ## Detiene el entorno (conserva datos)
	docker compose down

logs:
	docker compose logs -f

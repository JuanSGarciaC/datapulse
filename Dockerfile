# syntax=docker/dockerfile:1
# Imagen única de la aplicación Python (pipeline, API y migraciones comparten código).
# Multi-stage: el builder tiene uv y caché; el runtime solo el entorno virtual.

ARG PYTHON_VERSION=3.13

FROM ghcr.io/astral-sh/uv:0.11.33 AS uv

FROM python:${PYTHON_VERSION}-slim AS builder
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app

# 1) Dependencias primero: esta capa se reutiliza mientras uv.lock no cambie.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# 2) Código del proyecto (instalación no editable: el runtime no necesita src/).
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

FROM python:${PYTHON_VERSION}-slim AS runtime
LABEL org.opencontainers.image.title="datapulse" \
      org.opencontainers.image.source="https://github.com/JuanSGarciaC/datapulse" \
      org.opencontainers.image.licenses="MIT"

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Usuario sin privilegios: el contenedor nunca corre como root.
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --home-dir /app --no-create-home app

WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app migrations ./migrations

USER app

# Por defecto aplica migraciones y crea usuarios de servicio (idempotente).
CMD ["sh", "-c", "alembic upgrade head && python -m datapulse.db.bootstrap"]

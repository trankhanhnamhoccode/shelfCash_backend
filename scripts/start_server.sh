#!/usr/bin/env sh
set -e

mkdir -p \
  /app/runtime/uploads \
  /app/runtime/results \
  /app/runtime/forecast_artifacts \
  /app/runtime/forecast_shadow_artifacts

alembic upgrade head

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --workers 1

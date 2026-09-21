FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# The project metadata is the production dependency authority.  Do not install
# the optional test or Kaggle dependency groups in the runtime image.
COPY pyproject.toml ./
COPY app ./app
COPY shelfcash_core ./shelfcash_core
COPY shelfcash_forecast ./shelfcash_forecast
COPY alembic ./alembic
COPY alembic.ini ./
COPY scripts/start_server.sh ./scripts/start_server.sh

RUN python -m pip install --no-cache-dir . \
    && mkdir -p /app/runtime/uploads /app/runtime/results /app/runtime/forecast_artifacts /app/runtime/forecast_shadow_artifacts \
    && chmod 755 /app/scripts/start_server.sh

CMD ["/app/scripts/start_server.sh"]

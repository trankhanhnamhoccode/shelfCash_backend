# Deploy ShelfCash Backend to Railway

This is a short-lived, single-service deployment for a 1--3 day demo. It
keeps the existing SQLite deployment model and the existing application
settings; it does not use PostgreSQL.

## Runtime layout

Railway runs the root `Dockerfile` and starts `scripts/start_server.sh`. Attach
**exactly one** Railway Volume at `/app/runtime` -- never at `/app`. The volume
keeps the filesystem state that corresponds to the persisted SQLite records:

```text
/app/runtime/
  shelfcash.db
  uploads/
  results/
  forecast_artifacts/
  forecast_shadow_artifacts/
```

Those are the current default paths from `app/config.py`. If
`FORECAST_DEBUG_EXPORT=true`, set `FORECAST_EXPORT_DIR=/app/runtime/forecast_debug`
too, so optional debug exports are persisted with their run artifacts.

## Railway setup

1. Push this repository to GitHub.
2. In Railway, create one web service from that repository and branch.
3. Confirm the root `Dockerfile` is detected. The committed `railway.toml`
   declares Docker, `/health`, a 300-second startup healthcheck timeout, and an
   on-failure restart policy. If the Railway UI does not import the legacy TOML
   configuration for a newly created service, set those same values in Service
   Settings; current Railway documentation is moving new projects to its
   Infrastructure-as-Code workflow.
4. Attach one persistent Volume at `/app/runtime`.
5. Add the deployment variables/secrets below in the Railway service Variables
   page. Do not put secrets in Git, the Dockerfile, or `railway.toml`.
6. In service scaling, use **exactly one replica**. Do not enable horizontal or
   multi-region scaling.
7. Generate a public domain in Railway networking.
8. Set `CORS_ORIGINS` to include the exact generated/deployed frontend origin
   (for example, `https://demo.example.com`), retaining a comma-separated list
   if local origins are also needed.
9. Confirm the healthcheck path is `/health`, deploy, then request
   `https://<service-domain>/health`; it must return HTTP 200.
10. Call an authenticated endpoint with the `X-ShelfCash-Key` header and the
    configured `SHELFCASH_API_KEY`.
11. If OpenRouter is configured, check `GET /api/v1/llm/health` with that API
    header and run the LLM-dependent behavior required by the demo. Railway
    Trial/network policy must permit outbound HTTPS to OpenRouter for those
    calls.
12. Run one minimal import followed by the normal forecast/Decision flow, then
    inspect Railway logs for startup, migration, and request errors.
13. Stop or delete the service after the demo if it is no longer required.

`/health` is public, only executes a lightweight `SELECT 1` database check,
and does not call OpenRouter/Qwen or perform business computation.

## Environment variables

Railway provides `PORT`. Do not set it manually: the startup script binds
Uvicorn to exactly `${PORT}` (with `8000` only as a non-Railway local fallback).

Required for a public deployment:

- `SHELFCASH_API_KEY`: a strong, unique secret. Existing authentication permits
  an empty key for local/tests, but **DO NOT host publicly with it unset**.
- `CORS_ORIGINS`: comma-separated allowed frontend origins; do not use `*` for
  this credentialed API. Include the real deployed frontend URL.

Optional/defaulted deployment configuration:

- `DATABASE_URL`: leave unset to use the current default
  `sqlite:///runtime/shelfcash.db`, which resolves to
  `/app/runtime/shelfcash.db` because the image working directory is `/app`.
  If explicitly set, keep it a SQLite URL within `/app/runtime`.
- `OPENROUTER_API_KEY`: enables existing OpenRouter/Qwen behavior. It is
  optional; current deterministic/fallback behavior permits startup without it.
- `ENVIRONMENT`, `LOG_LEVEL`, `APP_NAME`, `APP_VERSION`: normal application
  metadata/logging options.
- `UPLOAD_DIR`, `RESULT_DIR`, `FORECAST_ARTIFACT_ROOT`, and
  `FORECAST_SHADOW_ARTIFACT_ROOT`: normally leave at their current defaults.
  Any override that holds persisted state must remain below `/app/runtime`.
- `FORECAST_DEBUG_EXPORT`, `FORECAST_EXPORT_DIR`, forecast provider/model,
  horizon, upload-limit, strategy-expression, ingredient-synthesis, and all
  `OPENROUTER_*` tuning variables retain the defaults listed in
  [`.env.example`](../.env.example). They are optional, and use their existing
  application names unchanged.

## Startup and SQLite safety

Every container boot does the following:

```text
create current runtime directories
-> alembic upgrade head
-> exec uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1
```

`alembic upgrade head` is idempotent: a fresh Volume receives a new SQLite DB
and all existing migrations; an already-current Volume retains its data and
starts normally. The image does not seed or overwrite a database.

**Important SQLite warning:** use exactly **one Railway replica** and exactly
**one Uvicorn worker**. Do not add Gunicorn workers, horizontal scaling, or a
second service process against this database/volume.

The `/app/runtime` volume is demo persistence, not a long-term database
strategy. It may be deliberately reset between application versions under the
project's disposable-data policy. Reset the database and all associated runtime
files together so DB rows cannot outlive referenced artifacts.

## Local Docker smoke check

When Docker is available, build and run with a host directory mounted at the
same runtime boundary:

```sh
docker build -t shelfcash-backend .
mkdir -p /tmp/shelfcash-runtime
docker run --rm -p 8000:8000 -e PORT=8000 \
  -e SHELFCASH_API_KEY=replace-with-a-strong-local-secret \
  -v /tmp/shelfcash-runtime:/app/runtime shelfcash-backend
curl -f http://localhost:8000/health
```

Stop and start that container again with the same mount to verify that the
SQLite database and files remain under the one runtime boundary.

# ShelfCash Backend

ShelfCash is a FastAPI backend for store data import, demand forecasting,
ingredient planning, purchase-order operations, and decision assistance.

## Source of truth

- The running OpenAPI document (`/openapi.json`) is the API schema authority.
- Pydantic models and route tests are the implementation authority.
- [docs/README.md](docs/README.md) is the maintained documentation index.

Historical checkpoint reports, legacy contracts, and frontend drafts are kept
under [docs/archive/](docs/archive/); they are not current authority.

## Decision flow

```text
Import / operational data
→ forecast run
→ ingredient demand and procurement planning
→ Decision Run (persisted package)
→ Overall Summary + Ingredient Synthesis
→ /brief, /explanation, or /what-if read models
```

The backend owns forecast, BOM conversion, FEFO simulation, procurement
selection, risks, and persisted Decision Packages. Narrative layers only render
already-authoritative facts. Strategy presentation and ingredient synthesis are
deterministic by default; guarded LLM polish is an explicit configuration mode.

## Local development

Python 3.11+ is required.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
alembic upgrade head
python -m scripts.seed_database
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Set `OPENROUTER_API_KEY` only when using an enabled LLM task. The application
has deterministic fallbacks for unavailable providers.

## Useful commands

```powershell
pytest -q
python -m compileall -q app shelfcash_core tests
```

## API entry points

- Interactive API: `/docs`
- OpenAPI schema: `/openapi.json`
- Health: `GET /health`
- Decision brief: `GET /api/v1/decision-runs/{decision_run_id}/brief`

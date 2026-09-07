# ShelfCash Backend Current State

## Baseline

- **Date:** 2026-09-07 (Asia/Saigon)
- **Branch:** `main`
- **HEAD:** `1d7d0c0` (`docs: establish backend pre-refactor baseline`), tagged `pre-refactor-backend-2026-09`.
- **Origin:** `main` is 0 ahead / 0 behind `origin/main` at the baseline freeze.
- **Working tree:** clean before DOC-CLEANUP-0.
- **Runtime:** Python 3.13.13; project requirement Python >=3.11; FastAPI 0.140.7; SQLAlchemy 2.0.51; Pydantic 2.13.4.
- **Default persistence:** SQLAlchemy with SQLite at `sqlite:///runtime/shelfcash.db`; Alembic head observed through migration `20260827_0025` in tests.

## Current runtime architecture

ShelfCash Backend is a FastAPI modular monolith. HTTP routes call application services, repositories/direct SQLAlchemy access, and deterministic computation adapters. `shelfcash_core` is the production computation authority. `shelfcash_forecast` is the optional forecast shadow/parity implementation; Decision Intelligence imports some non-computation contracts/helpers from it, which does not confer production authority. Persistence uses SQLAlchemy and SQLite by default. OpenRouter/Qwen is a bounded narrative or ambiguity-resolution integration; it is not business authority.

## Core business flow

Import → Canonical DB → Forecast → BOM → Ingredient Demand → Inventory/FEFO → Procurement candidates → Exact simulation/critic → Strategy selection → Decision Package → Decision Intelligence.

## Narrative / LLM status

- **Strategy Explanation:** deterministic DEFAULT; optional Qwen `llm_polish`; default `GET /brief` does not call Strategy Qwen.
- **Ingredient Synthesis:** deterministic DEFAULT for NORMAL/WATCH/CRITICAL; optional Qwen polish where configured; exact CRITICAL evidence authority remains enforced and missing exact evidence fails closed as `LIMITED_EVIDENCE`.
- **Overall Summary:** HYBRID; authorized facts → Qwen primary wording → strict backend validation → deterministic safe fallback. Deterministic-first migration is not accepted.
- **Decision Explanation:** grounded/guarded LLM where available, with deterministic/template fallback.
- **What-if:** deterministic business recomputation with grounded/hybrid explanation.
- **Excel Mapping:** deterministic/rule-first; LLM is reserved for genuine semantic ambiguity.

## API baseline

- Runtime OpenAPI: **58 paths / 70 operations**, matching `CURRENT_API_CONTRACT.md` by count.
- Restored route manifest: **58 unique paths / 70 operations**; exact manifest tests pass.
- Most `/api/v1` business routes require `X-API-Key`; health and framework documentation endpoints are public. OpenAPI exposes the API key as a header parameter rather than a security scheme.
- Runtime errors use `{code, message, details, request_id}`. Generated validation `422` documentation still advertises FastAPI `HTTPValidationError`, which is contract drift.
- Typed boundaries include import, ingredient catalog, recipes, forecast, legacy forecast/plan metadata/results, Decision Brief, Explanation, What-if, and inventory-constraint writes. Major untyped boundaries include raw Decision Package, IngredientDemandRun, ProcurementPlanRun, Menu/Product, operational page responses, completion writes, and Purchase Orders.

## Known technical debt

- `shelfcash_core` / `shelfcash_forecast` are duplicated/diverged computation implementations with authority-drift risk. `shelfcash_core` remains the production computation authority; helper/contract cross-imports do not make the shadow package a second production authority.
- Raw Decision Package `dict[str, Any]` API/persistence boundary without an explicit package schema version.
- Untyped IngredientDemandRun, ProcurementPlanRun, Menu/Product, operational `Page<T>`, completion-write, and Purchase Order responses.
- `DecisionPlanningService` spans demand, procurement, Decision Run generation/read, brief, summary/synthesis persistence, explanation, What-if, and legacy compatibility.
- Mixed repository and direct ORM usage; transaction ownership is not uniform.
- Canonical and legacy/compatibility APIs overlap without one explicit lifecycle registry.
- Persisted JSON blobs retain compatibility through permissive reads rather than explicit schema-version migration.
- OpenRouter transport/guard complexity remains broad relative to the reduced default LLM scope.
- Dense responsibility hotspots are confirmed in planning/completion route and service modules.

## R0 verification

- All 19 pre-existing tracked deletions were restored exactly from HEAD.
- Full suite: **577 passed, 0 failed, 0 skipped, 22 warnings in 142.39 seconds**.
- Route-manifest and frontend-document dependency tests pass.
- OpenAPI generation remains **58 paths / 70 operations**.
- Python compile and Git whitespace gates pass.
- Initial migration, import-fixture, and logger-capture failures were classified and repaired without changing runtime business behavior.
- The reviewed baseline is committed and tagged `pre-refactor-backend-2026-09`.

## Frozen invariants

- Backend owns business facts and recommendations; LLM may only word authorized evidence.
- New-item Opportunity Recommendation remains outside active-SKU M1–M6 paths.
- Selected, conservative-design, and stress scenarios retain separate authority.
- Strategy and Ingredient presentation are deterministic by default; Overall Summary remains hybrid.
- Cutoff D is an EOD boundary; future planning begins D+1.
- `fill_rate` is “tỷ lệ đáp ứng nhu cầu”; limitation/stress warnings are not silently converted into hard failures.
- Public contracts and historical Decision Runs are preserved unless an explicit migration is approved.

## Current phase

**DOC-CLEANUP-0 COMPLETE — PRE-REFACTOR BASELINE FROZEN**

## Next phase

**R1 — CONTRACT HARDENING.** R1 is not started. The recommended first slice remains **R1.1 — Common error/OpenAPI contract alignment**.

## Explicitly NOT doing yet

- No big-bang core merge.
- No `DecisionPlanningService` rewrite.
- No database redesign.
- No public API redesign.
- No deterministic-first Overall Summary migration.
- No removal of all Qwen code.
- No folder-wide restructure.
- No historical Decision Run rewrite.

## DOC-CLEANUP-0

- Canonical documentation set established and retained at the top level of `docs/`.
- Historical documentation archived under `docs/archive/`; redundant superseded documents deleted after reference review.
- R1 remains inactive; the recommended first slice is R1.1.

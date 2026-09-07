# ShelfCash Decision Core integration

## Current architecture (audited)

`POST /stores/{store_id}/forecast-runs` creates persisted `forecast_runs` and
`forecast_predictions`.  The planning API then runs `IngredientDemandRun` and
`ProcurementPlanRun`; the legacy `/plan-runs` endpoint is an adapter over that
flow.  Idempotency is persisted for all creation requests.

Canonical operational tables already include recipes/versioned recipe lines,
inventory lots and movements, supplier ingredient terms, inventory constraints,
purchase orders, forecasts, ingredient-demand runs and procurement-plan runs.

## Integration boundary

`shelfcash_core` is pure computation.  It receives Pydantic contracts only;
it never imports FastAPI, SQLAlchemy, sessions, or backend repositories.

```text
Canonical DB
  -> app.services.decision.adapters
  -> shelfcash_core
  -> backend persistence/API
```

The first production integration is `CoreBomAdapter`: it converts persisted
forecast predictions and effective Recipe/BOM versions into core contracts,
then preserves the existing ingredient-demand API contract.  Missing product
units are surfaced as the core's recoverable `PRODUCT_UNIT_MISSING` warning.

`CoreProcurementAdapter` is intentionally not enabled by default.  It builds a
core `OptimizationRequest` using lots, open POs, supplier terms and the
canonical `BudgetResolver`.  With the current database schema residual history
is not persisted, so it creates an *unweighted* P25/P50/P75 design set and
disables stochastic SAA; it never invents probabilities.  Enabling it requires
a persisted Decision Package contract and endpoint-level regression tests.

## Compatibility

All first-party imports now use `shelfcash_core`.  `ForecastService._core_config()`
continues to accept a config-like object exposing `to_dict()` at its boundary,
which keeps test and embedding configuration injection explicit without
retaining a duplicate package.

`DECISION_ENGINE_MODE` defaults to `legacy`; do not label its result as
stochastic SAA or robust optimization.

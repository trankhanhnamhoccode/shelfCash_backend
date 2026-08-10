# Decision Runs API

The unified endpoint is feature-gated. Set `DECISION_ENGINE_MODE=deterministic`
for the exact FEFO deterministic path, or `stochastic` when genuine residual
history is available. `legacy` remains the default and keeps existing planning
endpoints unchanged.

```bash
curl -X POST http://localhost:8000/api/v1/stores/STORE_001/decision-runs \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: decision-demo-001' \
  -d '{"forecast_run_id":"FORECAST_RUN_ID","as_of_date":"2026-08-10","horizon_days":7,"engine_mode":"deterministic","include_open_purchase_orders":true}'
```

```bash
curl http://localhost:8000/api/v1/decision-runs/DECISION_RUN_ID
curl -X POST http://localhost:8000/api/v1/decision-runs/DECISION_RUN_ID/explanation -H 'Content-Type: application/json' -d '{"language":"vi","detail_level":"simple"}'
curl -X POST http://localhost:8000/api/v1/decision-runs/DECISION_RUN_ID/what-if -H 'Content-Type: application/json' -d '{"demand_multiplier":1.3,"supplier_delay_days":2}'
```

The current what-if response is explicitly read-only and uses a persisted exact
stress result when one matches; it does not mutate inventory, orders, or the
source decision run. A re-optimization snapshot path is still required for
arbitrary budget/strategy what-ifs.

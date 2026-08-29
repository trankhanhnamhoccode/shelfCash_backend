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

## Deterministic ingredient-metric basis

For newly created Decision Runs, each item in
`business_metrics.deterministic.ingredient_metrics` is mathematically coherent:
its flat `demand_quantity`, `fulfilled_quantity`, `shortage_quantity`,
`fill_rate`, stockout, expiry, waste, and inventory fields all come from the
single scenario identified by `basis_scenario_id`, `basis_scenario_name`, and
`basis_kind`. The deterministic conservative selector chooses that complete
scenario by lowest fill rate, then highest shortage, then earliest stockout,
then scenario ID; it never uses scenario input order.

`scenario_metrics` retains each complete design-scenario row. `worst_case`
contains independently conservative values (`minimum_fill_rate`,
`maximum_shortage_quantity`, and `earliest_stockout`), each with the scenario
that supplied it. Those values are not a single algebraically comparable row.
Existing persisted packages are read as stored and are not rewritten by GET.

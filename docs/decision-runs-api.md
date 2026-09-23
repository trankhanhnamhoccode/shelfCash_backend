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

The what-if response is explicitly read-only. It builds an in-memory
hypothetical Decision Package from the persisted baseline inputs and requested
mutations; it never mutates inventory, orders, or the source Decision Run.

## Stochastic evidence diagnostics

For a newly created stochastic Decision Run, `technical_metrics` may include
the additive diagnostics `scenario_count_requested`,
`scenario_count_generated`, `unique_scenario_count`,
`effective_scenario_count`, and `stochastic_fallback_reason`.

`scenario_count_requested` is the caller's requested bootstrap count, while
`scenario_count_generated` is the count actually materialized before
ingredient-path deduplication. For a small eligible coherent-block pool
(`K <= 100`), generation samples without replacement when the request is below
`K`, and enumerates all `K` blocks once when the request is at least `K`.
Consequently `scenario_count_generated` can be less than the request. The
bounded `technical_metrics.scenario_diagnostics` object records
`eligible_coherent_block_count`, `selected_coherent_block_count`,
`distinct_selected_coherent_block_count`, and
`coherent_block_sampling_mode` (`without_replacement`, `enumerate_all`, or
`bootstrap_with_replacement`) for residual-bootstrap runs.

Generated records with identical business demand paths are aggregated before
stochastic risk authority is granted. If the effective scenario count is below
the current minimum of 10, the engine uses the ordinary p25/p50/p75 design
scenarios, publishes `stochastic_saa_enabled=false` and
`stochastic_fallback_reason="insufficient_effective_scenarios"`, and does not
use the insufficient sample to emit `RISK_CONSTRAINT_VIOLATION`. This fallback
does not make a candidate feasible by itself: Exact FEFO, safety-floor, service,
supplier, budget, capacity, and other existing rules continue to apply. These
fields are optional so stored historical packages remain readable as-is.

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

# Decision Intelligence API

Frontend-ready TypeScript contract: [FE_DECISION_RUNTIME_CONTRACT.md](FE_DECISION_RUNTIME_CONTRACT.md).

## Grounded narrative generation

`DecisionBriefFacts → deterministic evidence → deterministic aggregation → question-aware retrieval → OpenRouter Qwen → grounding guard → explanation`.

Qwen is an optional narrative layer only: it has no authority to forecast, calculate BOM, optimize procurement, alter risk, write data, or create purchase orders. It receives compact structured evidence, never raw database dumps. Daily demand is retained for date-specific questions; totals/min/max/peak are calculated in application code before Qwen receives them.

ShelfCash uses OpenRouter (`qwen/qwen3.5-9b`) when `OPENROUTER_API_KEY` is configured. Invalid JSON, timeout, unavailable OpenRouter, unsupported evidence IDs, entity mismatches, or unsupported numbers fall back to the deterministic explanation without failing the endpoint.

For example, with grounded demand and procurement evidence, the question `Tại sao phải nhập thêm sữa?` can return: `Kế hoạch ghi nhận đặt 60 lít sữa tươi.` The answer can only state a cause such as inventory shortage when the retrieved evidence explicitly supports it.

## GET `/api/v1/decision-runs/{decision_run_id}/brief`

Returns typed, read-only `DecisionBriefFacts` derived from the persisted decision package.

```json
{"decision_run_id":"...","store_id":"STORE_001","status":"completed","forecast":{"forecast_run_id":"...","model_version":"...","horizon_days":7,"cutoff_date":"2026-08-19"},"recommendation":{"available":true,"strategy":"balanced","summary":"Persisted production recommendation.","total_purchase_cost":null,"expected_fill_rate":null},"procurement_rows":[{"ingredient_id":"milk","ingredient_name":"Sữa","supplier_id":"supplier-1","supplier_name":"Supplier A","quantity":24,"unit":"lít","pack_count":null,"pack_size":null,"order_date":null,"arrival_date":null,"purchase_cost":null,"reason_codes":[]}],"ingredient_demand":[],"risk":{"stockout_probability":null,"expected_fill_rate":null,"shortage_quantity":null,"waste_quantity":null},"critic":{"hard_violations":[],"warnings":[]},"evidence":[],"data_availability":{"stockout_probability":"UNAVAILABLE"},"generated_at":"2026-08-19T00:00:00Z"}
```

`completed_with_no_feasible_recommendation` always has `recommendation.available=false` and an empty `procurement_rows` list. `stockout_probability` is nullable; quantiles or stress scenarios are not probabilities.

```ts
interface IngredientDemandRow {
  ingredient_id: string;
  ingredient_name: string | null;
  target_date: string; // ISO business date, e.g. "2026-08-20"
  unit: string | null;
  p25: number | null;
  p50: number | null;
  p75: number | null;
  contributions: unknown[];
}
```

Seven rows for the same ingredient in a seven-day horizon are seven **daily** demand observations, not duplicates and not scenarios. P25/P50/P75 describe uncertainty for that specific `target_date`. The API orders rows by `target_date`, then ingredient ID/name.

Strategies are `lean`, `balanced`, and `protected`. Frontend must render the order plan from `procurement_rows`, never by parsing an explanation.

## POST `/api/v1/decision-runs/{decision_run_id}/explanation`

Request:

```json
{"language":"vi","detail_level":"simple","question":"Tại sao phải nhập sữa?"}
```

`question` is optional for compatibility. `language` is `vi` or `en`; `detail_level` is `simple`, `manager`, or `technical`.

The response retains legacy fields (`source`, `summary`, `why_this_plan`, `main_risks`) and adds `answer`, `intent`, `entities`, citation-bearing `claims`, `citations`, `grounded`, and `provider`. Every citation identifies evidence derived from the same persisted decision package.

## POST `/api/v1/decision-runs/{decision_run_id}/what-if`

Runs an in-memory production-core hypothetical. It never modifies the baseline decision run, inventory, supplier constraints, POs, or planning tables.

```json
{"demand_multiplier":1.2,"supplier_delay_days":2,"budget_limit":10000000,"strategy":"protected"}
```

Response fields are `baseline`, `hypothetical`, `mutations`, and `comparison`.

```json
{"decision_run_id":"...","baseline":{"recommendation":{"available":true}},"hypothetical":{"recommendation":{"available":false},"data_availability":{"authority":"HYPOTHETICAL"}},"mutations":{"demand_multiplier":1.2,"supplier_delay_days":2,"budget_limit":10000000,"strategy":"protected"},"comparison":{"recommendation_changed":true,"baseline_strategy":"balanced","hypothetical_strategy":null,"purchase_cost_delta":null,"expected_fill_rate_delta":null,"stockout_probability_delta":null,"shortage_quantity_delta":null,"waste_quantity_delta":null,"order_changes":[],"warnings_added":[],"warnings_removed":[],"hard_violations_added":[],"hard_violations_removed":[]},"grounded_explanation":null,"generated_at":"2026-08-19T00:00:00Z"}
```

`null` means a metric was unavailable; it never means zero. Render `baseline.procurement_rows` and `hypothetical.procurement_rows` side-by-side rather than parsing the explanation.

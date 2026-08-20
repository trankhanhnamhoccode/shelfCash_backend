# ShelfCash Decision Assistant — Frontend Integration Guide

> Schema authority: [Decision Assistant API Contract — Phase 7 Freeze](decision_assistant_api_contract.md). This guide explains how to use that frozen contract; it does not replace it. Tested, representative payloads are in [decision_assistant_frontend_examples.json](examples/decision_assistant_frontend_examples.json).

## Read this first

```text
BACKEND CONTRACT:              FROZEN
DETERMINISTIC DECISION DATA:   VALIDATED
MOCKED LLM / FALLBACK PATHS:   VALIDATED
LIVE OPENROUTER / QWEN:        PENDING EXTERNAL VALIDATION
FULL BACKEND SUITE:            INCOMPLETE (300-second cap)
WHAT-IF FULL SNAPSHOT REPLAY:  NOT GUARANTEED
```

Use `/api/v1/decision-runs/{id}/brief` as the normal Decision Assistant read model. The raw Decision Run routes preserve technical package compatibility and are not the primary UI contract.

## Recommended UI flow

```text
POST /stores/{store_id}/decision-runs
        -> retain decision_run_id
GET  /decision-runs/{decision_run_id}/brief
        -> render Decision Assistant page

Optional interaction:
POST /decision-runs/{decision_run_id}/explanation  -> manager explanation
POST /decision-runs/{decision_run_id}/what-if      -> scenario simulation
```

Suggested frontend states:

```text
idle -> creating_decision -> decision_ready -> loading_brief -> brief_ready
                                          \-> no_feasible_recommendation

brief_ready -> explaining
brief_ready -> simulating_what_if
```

`assistant_summary.source` and `grounded_explanation.source` are independent presentation substates: `llm`/`openrouter_qwen` and `deterministic_fallback` are both successful responses.

## Endpoint guide

| Method | Route | Use in UI | Stability |
| --- | --- | --- | --- |
| POST | `/api/v1/stores/{store_id}/decision-runs` | Start a decision computation | Technical creation response; retain its `decision_run_id` |
| GET | `/api/v1/decision-runs/{decision_run_id}` | Diagnostics only | Technical / compatibility |
| GET | `/api/v1/decision-runs/{decision_run_id}/brief` | Main Decision Assistant screen | Stable UI read model |
| POST | `/api/v1/decision-runs/{decision_run_id}/explanation` | General or ingredient “Why?” interaction | Stable UI plus evidence diagnostics |
| POST | `/api/v1/decision-runs/{decision_run_id}/what-if` | Scenario simulation | Stable UI plus evidence diagnostics |

### Create a Decision Run

Only this route supports `Idempotency-Key`. Generate one per user submission and reuse it only when retrying the same logical request. A new run may create `PLAN_SUMMARY` before the POST returns, so show a progress state, avoid duplicate POSTs, and do not assume a numeric latency target.

```http
POST /api/v1/stores/store-demo/decision-runs
Idempotency-Key: 5ec0af0d-11f5-4c00-9e75-5b6b06c8f3ad
Content-Type: application/json
```

```json
{
  "forecast_run_id": "forecast-demo-2026-08-20",
  "as_of_date": "2026-08-20",
  "horizon_days": 7,
  "engine_mode": "deterministic",
  "include_open_purchase_orders": true,
  "budget_override": null,
  "scenario_count": null,
  "random_seed": null
}
```

The POST result is a raw technical package. Extract and retain its `decision_run_id`, then request `/brief`; do not bind normal UI components to arbitrary nested package fields.

## `/brief`: primary UI read model

The following fields are required and non-null in the brief: `decision_run_id`, `store_id`, `status`, `forecast`, `recommendation`, `risk`, `critic`, and `generated_at`. Lists normally serialize as `[]`; `assistant_summary` and `strategy_comparison` may be `null`.

| Backend field | Recommended UI destination | Display rule | Null / semantic rule |
| --- | --- | --- | --- |
| `forecast` | Context line | show horizon/cutoff if present | individual values may be null |
| `recommendation` | Plan status card | branch first on `available` | `false` is valid no-feasible business state |
| `procurement_rows` | Procurement table | ingredient, supplier, quantity/unit, cost, dates | empty when no plan; do not fabricate rows |
| `ingredient_demand` | Daily chart | use `target_date`, P25/P50/P75 | daily grain only |
| `ingredient_demand_summary` | Ingredient horizon cards | totals, min/max, peak | canonical backend aggregation |
| `risk` | Numeric KPI area | show only non-null metrics | null is not zero/safe |
| `risk_details` | Risk/limitation cards | render registry fields directly | not a local code dictionary |
| `critic` | Technical/detail drawer | raw hard violations/warnings | do not replace `risk_details` with it |
| `strategy_comparison` | Strategy comparison section | candidates and selected-relative deltas | hide section if null |
| `assistant_summary` | Overall Decision card | headline, summary, bullets, warning | fallback is still a success |
| `evidence`, `data_availability` | Optional evidence/debug UI | only when product needs it | do not make ordinary UI depend on them |
| `generated_at` | “Generated at” label | format in store locale | timestamp, not a freshness guarantee |

### Overall Decision card

Render in this order when `assistant_summary` is non-null:

1. `headline`
2. `summary`
3. `key_points[]` as optional bullets
4. `warning_summary` when non-null

`source: "deterministic_fallback"` means LLM wording was unavailable, invalid, or rejected; deterministic decision data is still valid. Do **not** show an error banner or hide recommendation, demand, risks, or strategy comparison because of fallback. Developer-only diagnostics may show the source.

### Recommendation and procurement

If `recommendation.available` is true, show `strategy`, `total_purchase_cost`, and `expected_fill_rate` only when non-null. If false, render a valid “ShelfCash has not found a feasible procurement recommendation under the current conditions” state. Do not display null money as `0 ₫` or null fill rate as `0%`.

Each procurement row contains `ingredient_id`, `ingredient_name`, `supplier_id`, `supplier_name`, `quantity`, `unit`, `pack_count`, `pack_size`, `order_date`, `arrival_date`, `purchase_cost`, and `reason_codes`.

`reason_codes` are raw diagnostic compatibility data. They are **not** frontend permission to create causal prose. In particular, `PACK_SIZE_ROUNDING` does not permit “the quantity was chosen because of pack size.” Use the backend explanation endpoint for manager-facing explanation.

### Daily demand versus horizon demand

Use `ingredient_demand` for a daily P25/P50/P75 chart. It has one ingredient/date row and must never be labeled a horizon total.

Use `ingredient_demand_summary` for horizon cards. `aggregation_method: "sum_daily_quantiles"` means `p25_total`, `p50_total`, and `p75_total` are sums of daily model quantile outputs. Do not label them as independently estimated “7-day distribution quantiles,” and do not recompute card totals from chart rows when the summary exists.

### Numeric risk and `risk_details`

For `risk.stockout_probability`, `risk.expected_fill_rate`, `risk.shortage_quantity`, and `risk.waste_quantity`:

```text
non-null -> render with its unit/percentage convention
null     -> show “Chưa có dữ liệu” or hide the metric
```

Never map null probability to `0%`, “An toàn”, or “Rủi ro thấp”.

Render `risk_details[]` without a frontend dictionary:

| Field | UI use |
| --- | --- |
| `title` | card title |
| `meaning` | card description; may be null |
| `recommended_action` | optional next-action text; may be null |
| `classification` | group as `risk`, `limitation`, or `unknown` |
| `category` | icon/grouping |
| `severity` | visual priority: `info`, `warning`, `critical` |
| `code` | developer/debug detail only |

A `limitation` means ShelfCash could not completely evaluate data/configuration/model scope; it is not an observed bad outcome. `unknown` preserves an unmapped backend code without invented meaning. Severity is presentation priority, never probability or certainty.

### Strategy comparison

Use backend `label` values for `lean`, `balanced`, and `protected`; do not recreate label mapping in frontend. Candidate metrics are nullable and must be compared only when supplied.

**Important direction:** every `candidate.vs_selected` scalar delta is:

```text
selected strategy - this candidate
```

For example, `purchase_cost_delta: 1000000` means the selected strategy costs 1,000,000 more than that candidate.

`selection_reason.available` is the gate for a “Why this strategy?” UI. For new runs with proof, the backend rule is valid (`critic.passed`) candidates, lowest purchase cost, then strategy-name ascending tie-break. When unavailable—especially for old runs—do not infer “highest fill rate”, “safest”, or any other reason from comparison metrics.

## Explanation interactions

### General explanation

```http
POST /api/v1/decision-runs/{decision_run_id}/explanation
Content-Type: application/json
```

```json
{
  "language": "vi",
  "detail_level": "simple",
  "question": "Tại sao kế hoạch này đáng chú ý?"
}
```

Primary UI fields are `answer`, `summary`, `why_this_plan`, `main_risks`, `tradeoffs`, and `important_assumptions`. `claims`, `citations`, `entities`, and `grounded` support optional evidence UX. Treat `provider` and especially `raw_response` as technical diagnostics; do not render `raw_response`.

### Ingredient “Why?” action

For a procurement/demand row, always send its stable `ingredient_id`:

```json
{
  "language": "vi",
  "detail_level": "simple",
  "ingredient_id": "ingredient-banana",
  "question": "Vì sao cần nhập nguyên liệu này?"
}
```

`ingredient_id` is authoritative. Question wording cannot redirect the explanation to another ingredient. `DECISION_RUN_INGREDIENT_NOT_FOUND` is HTTP 422: do not retry with a different ingredient or silently substitute a general explanation.

Questions are not business evidence. A question such as “Why did pack 5 kg make the order 30 kg?” can be answered only with trusted facts; frontend must not pre-fill or endorse causal wording from `reason_codes`.

## What-if interaction

Send only explicit mutations; all four request fields are optional.

```json
{
  "demand_multiplier": 1.1,
  "supplier_delay_days": null,
  "budget_limit": null,
  "strategy": null
}
```

`demand_multiplier` is a multiplier, not a percentage: `1.2` means +20%. `supplier_delay_days` is a global added supplier/open-inbound delay. `budget_limit` is scenario input, not proof the budget bound the outcome. `strategy` is a user-requested override, not an optimizer selection.

Use canonical response fields:

```text
mutation_facts.demand_change_percent
comparison.new_issues
comparison.resolved_issues
```

The compatibility aliases `demand_change_percentage_points`, `new_risks`, and `resolved_risks` remain serialized for old consumers, but new frontend code must not use them. `demand_change_percent: 20` renders as “Nhu cầu +20%”, never “+20 điểm phần trăm”.

### What-if delta direction

| Surface | Delta direction |
| --- | --- |
| Strategy `vs_selected` | selected strategy − candidate strategy |
| What-if `comparison` | hypothetical − baseline |

Thus `comparison.purchase_cost_delta: 1200000` means the hypothetical cost is 1,200,000 higher than the persisted baseline. Nullable deltas are unavailable, not zero. `order_changes[]` are unit-safe and use `added`, `removed`, `increased`, or `decreased`; never aggregate quantities across kilograms, litres, and pieces.

If `strategy_change.forced_by_request` is true, say “Kịch bản sử dụng chiến lược …”, not “ShelfCash selected …”. Primary What-if prose is `grounded_explanation.answer`; deterministic `baseline`, `hypothetical`, and `comparison` remain business authority regardless of narrative source.

## Error and business-state UX

| Condition | HTTP / response | Recommended UI |
| --- | --- | --- |
| Feasible decision | 2xx, `recommendation.available=true` | Render plan |
| No feasible recommendation | 2xx, `recommendation.available=false` | Valid no-plan state, not generic error |
| Summary/narrative fallback | 2xx, `source=deterministic_fallback` | Render normally; optionally expose developer diagnostic |
| Missing Decision Run | 404 `DECISION_RUN_NOT_FOUND` | Stale/missing resource state |
| Invalid/absent run ingredient | 422 `DECISION_RUN_INGREDIENT_NOT_FOUND` | Explain that row data is unavailable; do not substitute target |
| Invalid request | 422 `validation_error` or domain code | Show request/domain validation feedback |
| What-if prerequisite incomplete | 409 `INGREDIENT_DEMAND_INCOMPLETE` | Explain that scenario cannot be simulated from this run |
| Backend not ready | 503 `MODEL_NOT_READY` | Retry/reload readiness state; this is distinct from narrative fallback |

Error envelopes have the stable shape:

```json
{
  "code": "DECISION_RUN_INGREDIENT_NOT_FOUND",
  "message": "...",
  "details": {},
  "request_id": "..."
}
```

Branch on `code`, not human-readable `message` text.

## Old Decision Runs

No special frontend branch is normally needed. On older packages, `/brief` derives horizon summaries, `risk_details`, and comparison where possible; uses read-only deterministic `assistant_summary`; and does not trigger an LLM or mutate the Decision Run. Strategy selection proof remains unavailable without persisted proof.

## Mock-to-contract migration

| Old mock expectation | Frozen backend integration |
| --- | --- |
| Overall prose | `assistant_summary` |
| Horizon demand | `ingredient_demand_summary` |
| Daily demand | `ingredient_demand` |
| Human risk copy | `risk_details` |
| Strategy cards | `strategy_comparison` |
| “Why selected?” prose | `selection_reason.available`, then `/explanation` only when supported |
| Ingredient explanation | `/explanation` with `ingredient_id` |
| What-if prose | `comparison` plus `grounded_explanation` |
| Mock `risk_category` | Do not use unless an actual backend field provides it |
| Hard-coded probability | Use nullable backend metric only |
| Causal reason-code prose | Delete; never translate locally |

Suggested implementation order:

1. Replace mock creation and retain `decision_run_id`.
2. Replace mock brief with `/brief`.
3. Render assistant summary, recommendation, procurement, daily/horizon demand, risks, and strategy comparison.
4. Add ingredient explanation using explicit `ingredient_id`.
5. Add general explanation and What-if.
6. Remove mock-only prose, local risk-code dictionaries, local causal reason-code conversion, and deprecated alias usage.

## What frontend should normally ignore

- Raw `GET /decision-runs/{id}` package internals.
- `raw_response`, provider diagnostics, and evidence IDs unless building an explicit developer/evidence view.
- Raw critic warnings and `reason_codes` as natural-language inputs.
- Backend prompt/model internals.
- OpenRouter credentials: they remain backend-only.

## Known integration caveats

### Live provider validation

Phase 8 could not call OpenRouter/Qwen because `OPENROUTER_API_KEY` was unavailable. Real strict-schema acceptance, provider routing metadata, latency, token usage, and live linguistic quality remain external validation work. Mocked strict-schema, grounding, fallback, call-count, and deterministic-boundary tests pass. This is not a frontend error state.

### What-if snapshot semantics

Baseline is the persisted Decision Run. Hypothetical demand uses the persisted run demand snapshot plus requested mutation, while hypothetical supplier/inventory operational configuration is read through the current normal optimizer path. An identical What-if request can therefore change after operational configuration changes. Treat it as a current simulation based on the selected Decision Run, not a perfectly frozen historical replay.

### Engineering-only caveats

The backend collected 371 tests; the full run exceeded the 300-second cap at roughly 19% with no emitted failure. The targeted Decision Assistant suite passed. An isolated internal-module import cycle is also recorded as backend technical debt; normal application bootstrap works. Neither condition requires special frontend handling.


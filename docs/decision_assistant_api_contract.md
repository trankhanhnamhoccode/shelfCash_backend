# Decision Assistant API Contract — Phase 7 Freeze

Base path: `/api/v1`. This backend-owned document describes actual Pydantic/OpenAPI behavior, not historical frontend mock data.

## Scope and stability tiers

| Method | Path | Response | Tier |
| --- | --- | --- | --- |
| POST | `/stores/{store_id}/decision-runs` | raw persisted package object | Technical / compatibility |
| GET | `/decision-runs/{decision_run_id}` | raw persisted package object | Technical / compatibility |
| GET | `/decision-runs/{decision_run_id}/brief` | `DecisionBriefFacts` | Stable frontend contract |
| POST | `/decision-runs/{decision_run_id}/explanation` | `DecisionExplanationResponse` | Stable frontend plus diagnostics |
| POST | `/decision-runs/{decision_run_id}/what-if` | `WhatIfResponse` | Stable frontend plus diagnostics |

The raw-package routes are deliberately OpenAPI `object` responses with arbitrary properties. They preserve package compatibility and debugging access, but frontend should use `/brief` for UI projections.

## Shared error contract

Domain and validation errors have the shape:

```json
{"code":"...","message":"...","details":{},"request_id":"..."}
```

Relevant Decision Assistant errors: `DECISION_RUN_NOT_FOUND` (404), `DECISION_RUN_INGREDIENT_NOT_FOUND` (422), `FORECAST_INPUT_INVALID` (422), `INGREDIENT_DEMAND_INCOMPLETE` (409 on What-if prerequisite), `WHAT_IF_EXECUTION_FAILED` (422), `OPTIMIZATION_INFEASIBLE` (422), and request `validation_error` (422). `MODEL_NOT_READY` is a general 503 readiness error, not an LLM fallback response.

## Creation and raw read

`POST /stores/{store_id}/decision-runs` request fields are `forecast_run_id` (required string), `as_of_date` (required date), `horizon_days` (required integer 1..90), `engine_mode` (`legacy|deterministic|stochastic|null`), `include_open_purchase_orders` (boolean, default `true`), `budget_override` (integer >=0 or null), `scenario_count` (integer 1..1000 or null), and `random_seed` (integer or null).

Only creation supports `Idempotency-Key`. A matching key/request replays the same persisted raw package. The raw package is a technical surface, not a promise that every nested optimizer field is frontend-stable.

## Brief: primary UI read model

OpenAPI-required non-null fields are `decision_run_id`, `store_id`, `status`, `forecast`, `recommendation`, `risk`, `critic`, and `generated_at`. Collection/default fields `procurement_rows`, `ingredient_demand`, `ingredient_demand_summary`, `risk_details`, `evidence`, and `data_availability` are optional in the reusable Pydantic constructor and serialize as empty defaults in normal `/brief` responses. `strategy_comparison` and `assistant_summary` likewise serialize as `null` when unavailable; clients must accept null rather than treating absence as zero/false.

### Recommendation and procurement

`recommendation.available` is required. If false, `strategy`, `summary`, `total_purchase_cost`, and `expected_fill_rate` are null and `procurement_rows` is empty. This is a valid no-feasible-plan outcome. Numeric null never means zero or safe.

Each procurement row has `ingredient_id`, `ingredient_name|null`, `supplier_id|null`, `supplier_name|null`, `quantity`, `unit|null`, `pack_count|null`, `pack_size|null`, `order_date|null`, `arrival_date|null`, `purchase_cost|null`, and `reason_codes[]`. `reason_codes` are raw diagnostic/compatibility data, not independent causal permission. `PACK_SIZE_ROUNDING`, for example, can exist without trusted pre-round proof.

### Daily versus horizon demand

`ingredient_demand` is daily grain: ingredient/date, `target_date`, `p25`, `p50`, `p75`, `unit`, and optional `contributions`. It must never be used as a horizon total.

`ingredient_demand_summary` is per ingredient/horizon: `period_start`, `period_end`, `p25_total`, `p50_total`, `p75_total`, `daily_p50_min`, `daily_p50_max`, `peak_date`, `peak_p50`, and `aggregation_method="sum_daily_quantiles"`. Totals are sums of daily quantile outputs, not independently estimated horizon-distribution quantiles.

### Risk

`risk` has nullable `stockout_probability`, `expected_fill_rate`, `shortage_quantity`, and `waste_quantity`; null means unavailable/not evaluated/not applicable for that metric.

`risk_details[]` fields are `code`, `classification: risk|limitation|unknown`, `category`, `severity: info|warning|critical`, `title`, `meaning|null`, `recommended_action|null`, `scope: run|ingredient|supplier|strategy`, `ingredient_id|null`, `ingredient_name|null`, `evidence_ids[]`, and `source_count>=1`.

`risk` is an authoritative adverse observation. `limitation` means evaluation/data/config is incomplete and must not be rendered as an adverse event. `unknown` retains an unmapped code without invented meaning. Severity is deterministic presentation severity—not probability, confidence, or a quantitative score.

### Assistant summary lifecycle

`assistant_summary` has `headline`, `summary`, `key_points[]`, `warning_summary|null`, `source: llm|deterministic_fallback`, and `grounded`. A new run generates at most once after deterministic package persistence. Repeated `/brief` calls do not call an LLM. Old runs get a read-only deterministic fallback without package mutation or paid calls. `deterministic_fallback` is valid degraded success, not Decision Run failure.

### Strategy comparison

`strategy_comparison` is null when candidates cannot be projected. Otherwise it has `selected_strategy|null`, `candidates[]`, and `selection_reason`. Candidate fields: `strategy`, Vietnamese `label`, `selected`, `feasible`, `metrics`, `critic`, `vs_selected|null`, and `evidence_ids[]`. Metrics include nullable `purchase_cost`, `expected_fill_rate`, `stockout_probability`, `risk_evaluation_status`, and `risk_evaluation_method`.

`vs_selected` direction is **selected strategy minus candidate strategy**. Positive `purchase_cost_delta` means selected costs more. `selection_reason.available` is true only with replayable persisted proof. Current rule: `lowest_valid_candidate_cost_then_strategy_name`; eligible means `critic.passed`, primary metric is lowest purchase cost, tie-break is ascending strategy name. Old/unreconciled runs expose `available=false`; selected strategy alone is not a reason.

## Explanation

`POST /decision-runs/{id}/explanation` accepts `language: vi|en = vi`, `detail_level: simple|manager|technical = simple`, `question: string|null` (maximum 2000), and `ingredient_id: string|null` (1..255).

`ingredient_id`, when supplied, is authoritative; question wording cannot redirect it. Unknown/run-absent IDs return `DECISION_RUN_INGREDIENT_NOT_FOUND` (422), never an explanation for another ingredient. Question-only calls remain supported. Strategy comparison targeting is currently question-derived, not an explicit target field.

Primary UI fields are `answer`, `summary`, `why_this_plan`, `main_risks`, `tradeoffs`, and `important_assumptions`. Evidence fields are `claims`, `citations`, `entities`, and `grounded`. `source`, `provider`, and `raw_response|null` are diagnostics; frontend must not render raw response. Question text is not business evidence. LLM/network/schema/grounding failure returns deterministic fallback when deterministic evidence exists; invalid targets remain errors.

## What-if

Request fields are nullable: `demand_multiplier: number >0`, `supplier_delay_days: integer >=0`, `budget_limit: integer >=0`, and `strategy: lean|balanced|protected`.

`demand_multiplier` applies to persisted run demand. `supplier_delay_days` is a global added supplier/open-inbound delay. `budget_limit` is an input, not proof of binding. `strategy` is user-requested override, not optimizer selection.

Baseline is the persisted original package. Hypothetical demand uses that persisted demand snapshot, but the normal optimizer path reads current operational supplier/inventory configuration. What-if is therefore not a perfectly frozen full operational snapshot; Phase 8 must validate this runtime caveat.

The response has `decision_run_id`, `baseline`, `hypothetical`, typed `mutations`, `mutation_facts|null`, `comparison`, `grounded_explanation|null`, and `generated_at`.

`mutation_facts.demand_change_percent` is relative percent from multiplier 1.0 (`20` means +20%). Newly clarified `demand_change_percentage_points` is a deprecated compatibility alias with the same value; new consumers use `demand_change_percent`.

All What-if scalar deltas are **hypothetical minus baseline**. This differs from strategy comparison. Nullable deltas are unavailable, not zero. `order_changes[]` are ingredient/unit-safe and use `added|removed|increased|decreased`; mixed-unit totals are never manufactured.

`new_issues` and `resolved_issues` are canonical lists identified by `code`, `classification`, `scope`, and `ingredient_id`; both can contain risks or limitations. Deprecated `new_risks` and `resolved_risks` are compatibility aliases. A disappearing limitation must never be phrased as a resolved risk.

`grounded_explanation` uses `openrouter_qwen` when strict grounded wording succeeds and `deterministic_fallback` otherwise; its optional `authority` is `HYPOTHETICAL` in this response. Baseline, hypothetical, and comparison facts remain authoritative either way.

## Success, old runs, ownership, and mock reconciliation

| Condition | Public result |
| --- | --- |
| deterministic computation + Qwen success | normal success |
| deterministic computation + Qwen failure | successful deterministic fallback |
| no feasible recommendation | valid business response |
| invalid ingredient target | 422 error |

Old runs are read-only: horizon summary, risk details, and comparison are derived when data permits; selection proof is unavailable without persisted proof; assistant summary is deterministic fallback; GET does not mutate or call an LLM.

| Field | Owner | Frontend mock status |
| --- | --- | --- |
| `assistant_summary` | Decision Assistant presentation | Match; prose examples are style only |
| `ingredient_demand_summary` | Semantic Evidence | Different shape; use for horizon cards |
| `ingredient_demand` | persisted daily demand | Daily-only stable data |
| `risk_details` | deterministic metadata registry | Match conceptually; honor nullability |
| `strategy_comparison` | deterministic strategy projection | Metrics/probabilities not guaranteed |
| `selection_reason` | persisted selection proof | Not guaranteed for old runs |
| pack/MOQ/lead-time causal prose | no current trusted proof | Removed from contract |

## Freeze policy

This is the Phase-7 freeze. Before frontend handoff, bug fixes, nullable-field corrections, and runtime-correctness fixes are allowed. After Phase 9, prefer optional additive fields; any breaking change requires explicit migration/versioning discussion. Runtime base remains `/api/v1`; no `/v2` is introduced.

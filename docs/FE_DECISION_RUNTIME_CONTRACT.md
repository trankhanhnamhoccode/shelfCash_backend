# ShelfCash Decision Runtime — Frontend Contract (Archived Pre-Phase-7 Draft)

> This document is retained only for historical frontend-mock provenance.
> It is **not** an authoritative contract and must not be used for integration.
> Use [Decision Assistant API Contract — Phase 7 Freeze](decision_assistant_api_contract.md).

Base path: `/api/v1`.

## 1. Decision brief

`GET /decision-runs/{decisionRunId}/brief`

Use this response to render the persisted production plan. Do not derive order rows from explanation text.

```ts
export type Strategy = "lean" | "balanced" | "protected";

export interface DecisionBriefFacts {
  decision_run_id: string;
  store_id: string;
  status: string;
  forecast: {
    forecast_run_id: string | null;
    model_version: string | null;
    horizon_days: number | null;
    cutoff_date: string | null; // YYYY-MM-DD
  };
  recommendation: {
    available: boolean;
    strategy: Strategy | null;
    summary: string | null;
    total_purchase_cost: number | null;
    expected_fill_rate: number | null;
  };
  procurement_rows: ProcurementRow[];
  ingredient_demand: IngredientDemandRow[];
  risk: RiskSummary;
  critic: { hard_violations: string[]; warnings: string[] };
  evidence: EvidenceReference[];
  data_availability: Record<string, string>;
  generated_at: string; // ISO-8601
}

export interface ProcurementRow {
  ingredient_id: string;
  ingredient_name: string | null;
  supplier_id: string | null;
  supplier_name: string | null;
  quantity: number;
  unit: string | null;
  pack_count: number | null;
  pack_size: number | null;
  order_date: string | null;
  arrival_date: string | null;
  purchase_cost: number | null;
  reason_codes: string[];
}

export interface IngredientDemandRow {
  ingredient_id: string;
  ingredient_name: string | null;
  unit: string | null;
  p25: number | null;
  p50: number | null;
  p75: number | null;
  contributions: Array<Record<string, unknown>>;
}

export interface RiskSummary {
  stockout_probability: number | null;
  expected_fill_rate: number | null;
  shortage_quantity: number | null;
  waste_quantity: number | null;
}

export interface EvidenceReference {
  evidence_id: string;
  label: string;
  source_type: string;
  entities: Record<string, string>;
}
```

For `status === "completed_with_no_feasible_recommendation"`, `recommendation.available` is `false` and `procurement_rows` is always `[]`.

## 2. Grounded explanation

`POST /decision-runs/{decisionRunId}/explanation`

```ts
export interface ExplanationRequest {
  language?: "vi" | "en"; // default: vi
  detail_level?: "simple" | "manager" | "technical"; // default: simple
  question?: string | null;
}

export interface DecisionExplanationResponse {
  // Retained for legacy clients.
  source: string;
  language: "vi" | "en";
  detail_level: "simple" | "manager" | "technical";
  summary: string;
  why_this_plan: string[];
  main_risks: string[];
  tradeoffs: string[];
  important_assumptions: string[];

  decision_run_id: string;
  answer: string;
  intent: string;
  entities: { ingredient_ids: string[]; supplier_ids: string[] };
  claims: Array<{
    type: string;
    value: unknown;
    unit: string | null;
    evidence_ids: string[];
  }>;
  citations: Array<{ evidence_id: string; label: string; source_type: string }>;
  grounded: boolean;
  provider: "openrouter_qwen" | "shelfcash_decision_intelligence" | "deterministic_fallback" | "legacy_template_fallback";
  raw_response?: Record<string, unknown> | null;
}
```

Use `answer` for narrative only. To display numbers, orders, suppliers, or reasons, use `DecisionBriefFacts` and validate citations against `brief.evidence`.

## 3. What-if execution

`POST /decision-runs/{decisionRunId}/what-if`

```ts
export interface WhatIfRequest {
  demand_multiplier?: number | null; // > 0
  supplier_delay_days?: number | null; // >= 0
  budget_limit?: number | null; // >= 0
  strategy?: Strategy | null;
}

export interface WhatIfOrderChange {
  ingredient_id: string;
  baseline_quantity: number | null;
  hypothetical_quantity: number | null;
  quantity_delta: number | null;
  baseline_supplier_id: string | null;
  hypothetical_supplier_id: string | null;
  baseline_arrival_date: string | null;
  hypothetical_arrival_date: string | null;
}

export interface WhatIfResponse {
  decision_run_id: string;
  baseline: DecisionBriefFacts;
  hypothetical: DecisionBriefFacts;
  mutations: WhatIfRequest;
  comparison: {
    recommendation_changed: boolean;
    baseline_strategy: Strategy | null;
    hypothetical_strategy: Strategy | null;
    purchase_cost_delta: number | null;
    expected_fill_rate_delta: number | null;
    stockout_probability_delta: number | null;
    shortage_quantity_delta: number | null;
    waste_quantity_delta: number | null;
    order_changes: WhatIfOrderChange[];
    warnings_added: string[];
    warnings_removed: string[];
    hard_violations_added: string[];
    hard_violations_removed: string[];
  };
  grounded_explanation: {
    answer: string;
    citations: Array<{ evidence_id: string; label: string; source_type: string }>;
    grounded: boolean;
    authority: "HYPOTHETICAL";
  } | null;
  generated_at: string;
}
```

## UI rules

- Render baseline and hypothetical plans from their own `procurement_rows` side-by-side.
- `null` means `UNAVAILABLE`, never zero.
- `stockout_probability` is nullable; P75/stress is not a probability.
- Treat a no-feasible result as a valid business outcome, not an API failure.
- HTTP validation errors use the standard envelope: `{ code, message, details, request_id }`.

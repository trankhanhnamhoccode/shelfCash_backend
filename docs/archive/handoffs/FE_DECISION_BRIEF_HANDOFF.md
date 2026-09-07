# FE HANDOFF CONTRACT

## Endpoint

`GET /api/v1/decision-runs/{decision_run_id}/brief`

The existing endpoint is frozen. The presentation fields below are additive.

```ts
type RecommendationBrief = {
  available: boolean;
  strategy: "lean" | "balanced" | "protected" | null;
  // Plain persisted string, not an object. Null when no recommendation is available.
  summary: string | null;
  total_purchase_cost: number | null;
  expected_fill_rate: number | null;
};

type AssistantSummary = {
  headline: string;
  summary: string;
  key_points: string[];
  warning_summary: string | null;
  source: "llm" | "deterministic_fallback";
  grounded: boolean;
  raw_response: Record<string, unknown> | string | null;
  llm_diagnostics: Record<string, unknown> | null;
};

type IngredientSynthesis = {
  ingredient_id: string;
  ingredient_name: string | null;
  unit: string | null;
  importance: "normal" | "watch" | "critical";
  source: "rule_based" | "llm" | "deterministic_fallback";
  headline: string;
  summary: string;
  evidence_ids: string[];
};

type PresentedWarning = {
  code: string;
  severity: "info" | "warning" | "critical";
  audience: "user" | "technical";
  title: string;
  message: string;
};

type DecisionBriefPresentation = {
  recommendation: RecommendationBrief;
  assistant_summary: AssistantSummary | null;
  ingredient_synthesis: IngredientSynthesis[];
  // This manager-facing endpoint emits user warnings only. Treat [] as no warning.
  presented_warnings: PresentedWarning[];
};
```

`ingredient_synthesis` is normally present as an array and has one item per ingredient represented by the Decision Brief. Legacy Decision Runs receive deterministic items at read time without a provider call. `presented_warnings` is an array; no manager-facing warning is `[]`.

```json
{
  "recommendation": {
    "available": true,
    "strategy": "balanced",
    "summary": "Persisted production recommendation.",
    "total_purchase_cost": 1250000,
    "expected_fill_rate": 0.98
  },
  "assistant_summary": {
    "headline": "Kế hoạch hiện tại cần theo dõi một rủi ro chính",
    "summary": "Kế hoạch đã được lưu cùng dữ liệu chạy.",
    "key_points": [],
    "warning_summary": null,
    "source": "llm",
    "grounded": true,
    "raw_response": {"example": "provider output"},
    "llm_diagnostics": {"status": "success"}
  },
  "ingredient_synthesis": [
    {
      "ingredient_id": "ingredient-normal",
      "ingredient_name": "Sữa tươi",
      "unit": "lít",
      "importance": "normal",
      "source": "rule_based",
      "headline": "Kế hoạch chưa ghi nhận rủi ro thiếu hàng đáng kể",
      "summary": "Nhu cầu trong 7 ngày tới khoảng 16,22 lít. Kế hoạch hiện tại chưa ghi nhận rủi ro thiếu hàng đáng kể đối với nguyên liệu này.",
      "evidence_ids": ["ev-demand-normal"]
    },
    {
      "ingredient_id": "ingredient-critical",
      "ingredient_name": "Trân châu",
      "unit": "kg",
      "importance": "critical",
      "source": "llm",
      "headline": "Cần ưu tiên theo dõi rủi ro thiếu hàng",
      "summary": "Trân châu có thể thiếu từ 14/08 trong kỳ kế hoạch.",
      "evidence_ids": ["ev-risk-critical", "ev-order-critical"]
    },
    {
      "ingredient_id": "ingredient-fallback",
      "ingredient_name": "Bột cacao",
      "unit": "kg",
      "importance": "critical",
      "source": "deterministic_fallback",
      "headline": "Cần ưu tiên theo dõi rủi ro thiếu hàng",
      "summary": "Bột cacao có nguy cơ thiếu từ 15/08 trong kỳ kế hoạch.",
      "evidence_ids": ["ev-risk-fallback"]
    }
  ],
  "presented_warnings": [
    {
      "code": "CAPACITY_NOT_EVALUATED",
      "severity": "warning",
      "audience": "user",
      "title": "Chưa thể đánh giá đầy đủ sức chứa kho",
      "message": "Hệ thống còn thiếu thông tin cần thiết để kiểm tra khả năng lưu trữ."
    }
  ]
}
```

## Rendering rules

FE SHOULD render `recommendation.summary` as the plan-level decision summary, render `assistant_summary` separately as the overall narrative, render each `ingredient_synthesis` item directly, and render `presented_warnings` with its backend severity. Treat `source` as diagnostic provenance; it does not require an AI badge.

FE MUST NOT rebuild ingredient prose from `ingredient_demand` and `procurement_rows`, translate warning codes, calculate or round displayed quantities, infer importance, infer causes, choose AI eligibility, or expose raw internal warning codes as primary UI text.

Migration: replace frontend-built “AI Decision Synthesis” with a “Tóm tắt nguyên liệu” surface backed by `ingredient_synthesis`; replace raw warning-code UI with `PresentedWarning.title` and `message`; render the formerly unused `recommendation.summary` as “Tóm tắt quyết định”.

## Create/idempotency note

Create a Decision Run with `POST /api/v1/stores/{store_id}/decision-runs`. Send a stable `Idempotency-Key` header for a single user action. Repeating the same request with that key replays the existing result; reusing it for a different payload is a conflict. Without the header, rapid duplicate POSTs are independent runs.

# FRONTEND ACCEPTANCE CHECKLIST

1. No frontend-built ingredient narrative from demand/procurement rows remains.
2. Every returned ingredient synthesis item renders directly.
3. Normal, watch, and critical importance render safely.
4. LLM and deterministic fallback use the same layout.
5. `recommendation.summary` renders when non-null.
6. User warnings render title/message, never a translated code.
7. Technical diagnostics are not displayed in the normal manager warning area.
8. FE performs no numeric display calculation or rounding.
9. Ingredient names use `ingredient_name`; never display the ID as a normal label.
10. Empty `presented_warnings` renders cleanly.
11. Legacy Decision Runs render their deterministic synthesis.
12. Decision creation sends one stable `Idempotency-Key` per user action.

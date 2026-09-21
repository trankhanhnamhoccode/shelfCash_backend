# ShelfCash Backend — Frontend API Contract

Tài liệu này là bản index dành cho FE của toàn bộ API hiện tại. Nguồn schema
đầy đủ, có thể generate type/client, là runtime OpenAPI:

```text
GET /openapi.json
```

Base path business API: `/api/v1`. Hầu hết business routes dùng header
`x-shelfcash-key`; write routes có ghi `Idem` hỗ trợ `Idempotency-Key`.
Lỗi có shape `{code, message, details, request_id}`. Runtime hiện có **58 paths /
70 operations**.

Tài liệu chi tiết contract nền: `docs/CURRENT_API_CONTRACT.md`. Handoff Decision
Assistant: `docs/frontend_decision_assistant_integration.md`.

## Health và LLM

| Method | Path | FE dùng để |
|---|---|---|
| GET | `/health` | health check service/database |
| GET | `/api/v1/llm/health` | xem provider availability |
| POST | `/api/v1/llm/map-sheet` | gợi ý mapping cột Excel |

## Import Excel

| Method | Path |
|---|---|
| POST | `/api/v1/imports` |
| GET | `/api/v1/imports/{import_id}` |
| POST | `/api/v1/imports/{import_id}/confirm` |
| POST | `/api/v1/imports/{import_id}/process` |
| GET | `/api/v1/imports/{import_id}/result` |

## Catalog, menu và recipe

| Method | Path |
|---|---|
| GET, POST | `/api/v1/stores/{store_id}/ingredients` |
| PATCH | `/api/v1/stores/{store_id}/ingredients/{ingredient_id}` |
| GET, PUT | `/api/v1/stores/{store_id}/aliases` |
| GET, POST | `/api/v1/stores/{store_id}/products` |
| PATCH | `/api/v1/stores/{store_id}/products/{product_id}` |
| GET | `/api/v1/stores/{store_id}/menu` |
| PUT | `/api/v1/stores/{store_id}/products/{product_id}/components` |
| GET | `/api/v1/import-schemas` |
| GET, PUT | `/api/v1/stores/{store_id}/products/{product_id}/recipe` |
| GET | `/api/v1/stores/{store_id}/products/{product_id}/recipe-versions` |

## Operational reads

| Method | Path |
|---|---|
| GET | `/api/v1/stores/{store_id}/bootstrap` |
| GET | `/api/v1/stores/{store_id}/dashboard` |
| GET | `/api/v1/stores/{store_id}/imports` |
| GET | `/api/v1/stores/{store_id}/sales-history` |
| GET | `/api/v1/stores/{store_id}/usage-history` |
| GET | `/api/v1/stores/{store_id}/purchase-history` |
| GET | `/api/v1/stores/{store_id}/inventory` |
| GET | `/api/v1/stores/{store_id}/inventory-movements` |
| GET | `/api/v1/stores/{store_id}/settings` |
| GET | `/api/v1/stores/{store_id}/calendar-features` |
| GET | `/api/v1/business-constraint-types` |

## Operational writes và purchase orders

| Method | Path |
|---|---|
| POST | `/api/v1/stores/{store_id}/inventory-counts` |
| POST | `/api/v1/stores/{store_id}/inventory-adjustments` |
| POST | `/api/v1/stores/{store_id}/sales-history/batch` |
| POST | `/api/v1/stores/{store_id}/purchase-history/batch` |
| GET, POST | `/api/v1/stores/{store_id}/supplier-constraints` |
| PUT | `/api/v1/stores/{store_id}/supplier-constraints/{constraint_id}` |
| GET, POST | `/api/v1/stores/{store_id}/inventory-constraints` |
| PATCH | `/api/v1/stores/{store_id}/inventory-constraints/{constraint_id}` |
| POST | `/api/v1/stores/{store_id}/inventory-constraints/{constraint_id}/deactivate` |
| PUT | `/api/v1/stores/{store_id}/settings` |
| PUT | `/api/v1/stores/{store_id}/calendar-features` |
| POST | `/api/v1/stores/{store_id}/purchase-orders` |
| GET | `/api/v1/stores/{store_id}/purchase-orders` |
| GET, PATCH | `/api/v1/stores/{store_id}/purchase-orders/{po_id}` |
| POST | `/api/v1/stores/{store_id}/purchase-orders/{po_id}/confirm` |
| POST | `/api/v1/stores/{store_id}/purchase-orders/{po_id}/receive` |

## Forecast và compatibility runs

| Method | Path |
|---|---|
| POST | `/api/v1/forecast-models/train` |
| POST | `/api/v1/forecasts` — deprecated |
| GET | `/api/v1/forecasts/{forecast_run_id}` — deprecated |
| POST | `/api/v1/stores/{store_id}/forecast-runs` |
| GET | `/api/v1/stores/{store_id}/forecast-runs/{forecast_run_id}` |
| GET | `/api/v1/stores/{store_id}/forecast-runs/{forecast_run_id}/result` |
| POST | `/api/v1/stores/{store_id}/plan-runs` |
| GET | `/api/v1/stores/{store_id}/plan-runs/{plan_run_id}` |
| GET | `/api/v1/stores/{store_id}/plan-runs/{plan_run_id}/result` |

## Demand planning và Decision Assistant

| Method | Path | FE note |
|---|---|---|
| POST, GET | `/api/v1/stores/{store_id}/forecast-runs/{forecast_run_id}/ingredient-demand` | demand theo BOM |
| POST, GET | `/api/v1/stores/{store_id}/forecast-runs/{forecast_run_id}/procurement-plans` | operational procurement |
| POST | `/api/v1/stores/{store_id}/decision-runs` | tạo Decision Run |
| GET | `/api/v1/decision-runs/{decision_run_id}` | raw technical package |
| GET | `/api/v1/decision-runs/{decision_run_id}/brief` | UI read model chính |
| POST | `/api/v1/decision-runs/{decision_run_id}/explanation` | hỏi/giải thích tương tác |
| POST | `/api/v1/decision-runs/{decision_run_id}/what-if` | giả lập read-only |

### Quy tắc Decision FE

- Dùng `/brief` cho UI thông thường; không bind UI trực tiếp vào raw Decision
  Package.
- `recommendation.available=false` là business state hợp lệ, không phải HTTP
  failure.
- Hiển thị `strategy_selection_presentation` trực tiếp; không map reason code.
- Dùng `/explanation` chỉ cho câu hỏi tương tác; không dùng nó để tạo strategy
  cards mặc định.
- Null metric nghĩa là unavailable, không phải zero.

## Pagination, request IDs và idempotency

Các list page dùng `{items, page, page_size, total}`; mặc định `page=1`,
`page_size=50`, tối đa `200`. Gửi `X-Request-ID` nếu FE cần trace; backend sẽ
echo/generate ID. Với write operation hỗ trợ idempotency, FE phải tái dùng cùng
`Idempotency-Key` khi retry cùng một intent.

## FE review checklist

- Generate types từ `/openapi.json`, không tự viết lại request/response schema.
- Xử lý common error envelope theo `code`, không theo `message`.
- Không hiển thị raw LLM diagnostics hay `raw_response` cho end-user.
- Không suy business cause từ warning, reason code, critic hoặc scenario detail.
- Gọi đúng store-scoped route và lưu `decision_run_id` để đọc `/brief`.

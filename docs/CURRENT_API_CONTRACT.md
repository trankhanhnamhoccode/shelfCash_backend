# ShelfCash Backend — Current API Contract

> Contract inventory **as-is** tại ngày 2026-09-07, sinh từ `app.main.create_app()`, FastAPI OpenAPI, Pydantic schemas và service implementation. Runtime `/openapi.json` là schema authority. Tài liệu này giúp con người đọc và chỉ rõ những chỗ OpenAPI hiện chưa khóa được.

## 1. Contract snapshot

- API version prefix: `/api/v1`
- Runtime surface: **70 operations / 58 paths**
- Interactive docs: `GET /docs`
- OpenAPI: `GET /openapi.json`
- ReDoc: `GET /redoc`
- Content type mặc định: `application/json`
- Upload import: `multipart/form-data`

Ký hiệu dùng trong tài liệu:

- `T:Model` — response được FastAPI/Pydantic khóa bằng `response_model`.
- `U:{...}` — runtime trả JSON có shape này nhưng route chưa khai báo response model; đây là contract debt.
- `?` — field/parameter optional.
- `date` — `YYYY-MM-DD`; `datetime` — ISO-8601, một số write operation bắt buộc timezone.
- `decimal` — request nhận JSON number hoặc decimal string; nhiều response serialize thành string để giữ precision.

## 2. Common protocol

### Authentication

Mọi operation dưới `/api/v1` trừ `GET /api/v1/llm/health` dùng header:

```http
x-shelfcash-key: <configured key>
```

`GET /health` cũng public. Việc kiểm tra chỉ được enforce khi `SHELFCASH_API_KEY` khác rỗng; ở config mặc định development, header được OpenAPI mô tả optional và API mở.

### Request ID

Client có thể gửi:

```http
X-Request-ID: request-123
```

Server giữ ID hợp lệ hoặc tự sinh ID mới, trả lại ở response header và error body.

### Idempotency

Các endpoint có ghi `Idem` trong bảng nhận header optional:

```http
Idempotency-Key: <client-generated-key>
```

Cùng store + method + endpoint + key + request hash sẽ replay kết quả. Tái sử dụng key với payload khác tạo conflict domain. Idempotency hiện không áp dụng cho mọi write endpoint.

### Success and errors

Success body luôn là JSON trừ khi framework docs hoặc future route thay đổi. Actual error envelope chung:

```json
{
  "code": "DOMAIN_OR_VALIDATION_CODE",
  "message": "Human-readable message",
  "details": {},
  "request_id": "request-id"
}
```

Status thường gặp: `400/401/404/409/422/500/503` tùy domain error.

**Contract gap:** generated OpenAPI vẫn mô tả validation `422` bằng FastAPI `HTTPValidationError {detail:[...]}`, nhưng exception handler thực tế trả envelope ở trên với `details.errors`. Client nên dùng envelope thực tế; code nên bổ sung global OpenAPI error responses trong refactor.

## 3. Endpoint inventory

Common auth header được lược khỏi cột Input. Path params nằm ngay trong URL.

### 3.1 Health and LLM

| Method | Path | Input | Success output | Mục đích |
|---|---|---|---|---|
| GET | `/health` | — | `200 U:{status,service,version,database}` | kiểm tra API + database |
| GET | `/api/v1/llm/health` | — | `200 U:{provider,model,configured,available}` | public config health; không gọi model |
| POST | `/api/v1/llm/map-sheet` | `MapSheetRequest` | `200 T:MappingSuggestion` | rule-first, Qwen fallback/assist cho mapping sheet |

### 3.2 Imports

| Method | Path | Input | Success output | Mục đích |
|---|---|---|---|---|
| POST | `/api/v1/imports` | multipart `files[]`, `store_id`, `forecast_date?`, `forecast_horizon=7`; Idem | `201 T:ImportResponse` | upload/profile/suggest mapping |
| GET | `/api/v1/imports/{import_id}` | `import_id: UUID` | `200 T:StatusResponse` | đọc trạng thái và mappings |
| POST | `/api/v1/imports/{import_id}/confirm` | `ConfirmRequest` | `200 T:StatusResponse` | xác nhận/skip mappings |
| POST | `/api/v1/imports/{import_id}/process` | query `policy=atomic`; enum: `atomic`, `partial_success`, `preview_only` | `200 T:ProcessResponse` | normalize, validate, persist tùy policy |
| GET | `/api/v1/imports/{import_id}/result` | `import_id: UUID` | `200 U:ImportResult` | result/validation/business-write snapshot đã persist |

### 3.3 Catalog

| Method | Path | Input | Success output | Mục đích |
|---|---|---|---|---|
| GET | `/api/v1/stores/{store_id}/ingredients` | query `active?`, `q?`, `sku?` | `200 T:IngredientResponse[]` | list/filter ingredient |
| POST | `/api/v1/stores/{store_id}/ingredients` | `IngredientCreate`; Idem | `201 T:IngredientResponse` | tạo ingredient |
| PATCH | `/api/v1/stores/{store_id}/ingredients/{ingredient_id}` | `IngredientPatch` | `200 T:IngredientResponse` | optimistic update ingredient |
| GET | `/api/v1/stores/{store_id}/aliases` | query `ingredient_id?` | `200 T:AliasResponse[]` | list aliases |
| PUT | `/api/v1/stores/{store_id}/aliases` | `AliasBulkUpsert`; Idem | `200 T:AliasResponse[]` | bulk upsert aliases |
| GET | `/api/v1/stores/{store_id}/products` | query `active?`, `q?`, `sku?` | `200 U:MenuProduct[]` | list product, gồm combo details |
| POST | `/api/v1/stores/{store_id}/products` | `MenuProductCreate`; Idem | `201 U:MenuProduct` | tạo single/combo product |
| PATCH | `/api/v1/stores/{store_id}/products/{product_id}` | `MenuProductPatch` | `200 U:MenuProduct` | optimistic update product |

### 3.4 Menu and recipes

| Method | Path | Input | Success output | Mục đích |
|---|---|---|---|---|
| GET | `/api/v1/stores/{store_id}/menu` | `status=active`, `item_type=all`, `search?`, `page=1`, `page_size=50` | `200 U:{items:MenuProduct[],summary,page,page_size,total}` | read model menu |
| PUT | `/api/v1/stores/{store_id}/products/{product_id}/components` | `ComponentsReplace`; Idem | `200 U:MenuProduct` | thay toàn bộ combo components |
| GET | `/api/v1/import-schemas` | — | `200 U:{schemas:{sheet_type:{label,fields,core_fields,field_metadata}}}` | discover canonical import schemas |
| GET | `/api/v1/stores/{store_id}/products/{product_id}/recipe` | query `on_date?` | `200 T:ActiveRecipeResponse` | recipe active tại ngày |
| PUT | `/api/v1/stores/{store_id}/products/{product_id}/recipe` | `RecipeWrite`; Idem | `200 T:ActiveRecipeResponse` | tạo hoặc reuse recipe version |

### 3.5 Operational reads

Các list response ở nhóm này dùng `Page<T> = {items:T[], page:int, page_size:int, total:int}` nhưng chưa có generic response model.

| Method | Path | Input | Success output | Mục đích |
|---|---|---|---|---|
| GET | `/api/v1/stores/{store_id}/imports` | `status?`, `date_from?`, `date_to?`, paging | `200 U:Page<ImportSummary>` | lịch sử import |
| GET | `/api/v1/stores/{store_id}/products/{product_id}/recipe-versions` | paging | `200 U:Page<RecipeVersionSummary>` | list recipe versions |
| GET | `/api/v1/stores/{store_id}/sales-history` | `date_from?`, `date_to?`, `product_id?`, `source?`, paging | `200 U:Page<SalesRecord>` | sales history |
| GET | `/api/v1/stores/{store_id}/usage-history` | `date_from?`, `date_to?`, `ingredient_id?`, `source?`, paging | `200 U:Page<UsageRecord>` | ingredient usage history |
| GET | `/api/v1/stores/{store_id}/purchase-history` | `date_from?`, `date_to?`, `ingredient_id?`, `supplier_id?`, `source?`, paging | `200 U:Page<PurchaseRecord>` | purchase receipts/history |
| GET | `/api/v1/stores/{store_id}/inventory` | `ingredient_id?`, paging | `200 U:Page<InventoryLotView>` | lot balance + expiry status |
| GET | `/api/v1/stores/{store_id}/inventory-movements` | `lot_id?`, `ingredient_id?`, `movement_type?`, paging | `200 U:Page<InventoryMovement>` | movement ledger |
| GET | `/api/v1/stores/{store_id}/settings` | — | `200 U:StoreSettingsView` | settings + current budget state |
| GET | `/api/v1/stores/{store_id}/calendar-features` | `date_from?`, `date_to?`, paging | `200 U:Page<CalendarFeatureView>` | calendar read model |

Paging rules: `page >= 1`, `1 <= page_size <= 200`; default `page=1`, `page_size=50`.

### 3.6 Business constraint discovery

| Method | Path | Input | Success output | Mục đích |
|---|---|---|---|---|
| GET | `/api/v1/business-constraint-types` | — | `200 T:ConstraintTypeCatalogResponse` | canonical type/alias/scope/dimension/unit/value/planner support registry |

### 3.7 Operational writes, compatibility forecast/plan and purchase orders

| Method | Path | Input | Success output | Mục đích |
|---|---|---|---|---|
| GET | `/api/v1/stores/{store_id}/bootstrap` | — | `200 U:BootstrapView` | aggregate payload khởi tạo frontend |
| GET | `/api/v1/stores/{store_id}/dashboard` | — | `200 U:DashboardView` | KPI vận hành |
| POST | `/api/v1/stores/{store_id}/inventory-counts` | `CountIn`; Idem | `201 U:InventoryCountResult` | physical count và adjustment ledger |
| POST | `/api/v1/stores/{store_id}/inventory-adjustments` | `AdjustmentIn`; Idem | `201 U:InventoryAdjustmentResult` | manual inventory movement |
| POST | `/api/v1/stores/{store_id}/sales-history/batch` | `SalesBatchIn`; Idem | `201 U:SalesBatchResult` | batch sales + rebuild derived usage |
| POST | `/api/v1/stores/{store_id}/purchase-history/batch` | `PurchaseBatchIn`; Idem | `201 U:PurchaseBatchResult` | record-only purchase history |
| GET | `/api/v1/stores/{store_id}/supplier-constraints` | — | `200 T:SupplierTermList` | active supplier terms |
| POST | `/api/v1/stores/{store_id}/supplier-constraints` | `SupplierTermIn`; Idem | `201 U:SupplierTerm` | tạo supplier term |
| PUT | `/api/v1/stores/{store_id}/supplier-constraints/{constraint_id}` | `SupplierTermIn`; Idem | `200 U:SupplierTerm` | replace/update term theo version |
| GET | `/api/v1/stores/{store_id}/inventory-constraints` | `ingredient_id?`, `constraint_type?`, `as_of_date?` | `200 T:InventoryConstraintList` | list effective constraints |
| POST | `/api/v1/stores/{store_id}/inventory-constraints` | `InventoryConstraintCreateIn`; Idem | `201 T:InventoryConstraintWriteOut` | tạo version đầu |
| PATCH | `/api/v1/stores/{store_id}/inventory-constraints/{constraint_id}` | `InventoryConstraintPatchIn`; Idem | `200 T:InventoryConstraintWriteOut` | versioned update/correction |
| POST | `/api/v1/stores/{store_id}/inventory-constraints/{constraint_id}/deactivate` | `InventoryConstraintDeactivateIn`; Idem | `200 T:InventoryConstraintWriteOut` | đóng hiệu lực constraint |
| PUT | `/api/v1/stores/{store_id}/settings` | `SettingsIn` | `200 U:StoreSettingsView` | optimistic upsert settings/budget |
| PUT | `/api/v1/stores/{store_id}/calendar-features` | `CalendarIn` | `200 U:CalendarWriteResult` | bulk upsert calendar |
| POST | `/api/v1/stores/{store_id}/forecast-runs` | `ForecastIn`; Idem | `200 T:LegacyForecastMetadataResponse` | compatibility forecast run API |
| GET | `/api/v1/stores/{store_id}/forecast-runs/{forecast_run_id}` | — | `200 T:LegacyForecastMetadataResponse` | compatibility forecast metadata |
| GET | `/api/v1/stores/{store_id}/forecast-runs/{forecast_run_id}/result` | — | `200 T:LegacyForecastResultResponse` | compatibility forecast result |
| POST | `/api/v1/stores/{store_id}/plan-runs` | `PlanIn`; Idem | `200 T:LegacyPlanMetadataResponse` | compatibility plan adapter |
| GET | `/api/v1/stores/{store_id}/plan-runs/{plan_run_id}` | — | `200 T:LegacyPlanMetadataResponse` | compatibility plan metadata |
| GET | `/api/v1/stores/{store_id}/plan-runs/{plan_run_id}/result` | — | `200 T:LegacyPlanResultResponse` | compatibility plan result/recommendations |
| POST | `/api/v1/stores/{store_id}/purchase-orders` | `POCreateIn`; Idem | `201 U:{orders:PurchaseOrder[]}` | tạo draft PO, group theo supplier |
| GET | `/api/v1/stores/{store_id}/purchase-orders` | — | `200 U:Page<PurchaseOrder>` | list PO; hiện fixed page 1/50 |
| GET | `/api/v1/stores/{store_id}/purchase-orders/{po_id}` | — | `200 U:PurchaseOrder` | đọc PO |
| PATCH | `/api/v1/stores/{store_id}/purchase-orders/{po_id}` | `POPatchIn` | `200 U:PurchaseOrder` | sửa line của draft PO |
| POST | `/api/v1/stores/{store_id}/purchase-orders/{po_id}/confirm` | `POConfirmIn` | `200 U:PurchaseOrder` | reserve budget và chuyển ordered |
| POST | `/api/v1/stores/{store_id}/purchase-orders/{po_id}/receive` | `POReceiveIn`; Idem | `201 U:PurchaseOrder` | receive lots, inventory movements, spend budget |

### 3.8 Forecast-core API

| Method | Path | Input | Success output | Mục đích |
|---|---|---|---|---|
| POST | `/api/v1/forecast-models/train` | `ForecastTrainRequest` | `200 T:ForecastTrainingResponse` | train/version model artifacts |
| POST | `/api/v1/forecasts` | `ForecastPredictRequest` | `201 T:ForecastResponse` | predict; **deprecated** |
| GET | `/api/v1/forecasts/{forecast_run_id}` | query `store_id?` | `200 T:ForecastResponse` | đọc predict result; **deprecated** |

### 3.9 Demand planning and Decision Assistant

| Method | Path | Input | Success output | Mục đích |
|---|---|---|---|---|
| POST | `/api/v1/stores/{store_id}/forecast-runs/{forecast_run_id}/ingredient-demand` | Idem | `200 U:IngredientDemandRun` | refresh/generate BOM-expanded demand |
| GET | `/api/v1/stores/{store_id}/forecast-runs/{forecast_run_id}/ingredient-demand` | — | `200 U:IngredientDemandRun` | đọc demand run |
| POST | `/api/v1/stores/{store_id}/forecast-runs/{forecast_run_id}/procurement-plans` | `ProcurementPlansRequest`; Idem | `200 U:ProcurementPlanRun` | generate strategy candidates |
| GET | `/api/v1/stores/{store_id}/forecast-runs/{forecast_run_id}/procurement-plans` | query `procurement_plan_run_id?` | `200 U:ProcurementPlanRun` | latest hoặc specific plan run |
| POST | `/api/v1/stores/{store_id}/decision-runs` | `DecisionRunRequest`; Idem | `200 U:DecisionPackage` | compute + persist self-contained decision |
| GET | `/api/v1/decision-runs/{decision_run_id}` | — | `200 U:DecisionPackage` | raw persisted package |
| GET | `/api/v1/decision-runs/{decision_run_id}/brief` | — | `200 T:DecisionBriefFacts` | primary frontend read model |
| POST | `/api/v1/decision-runs/{decision_run_id}/explanation` | `ExplanationRequest` | `200 T:DecisionExplanationResponse` | grounded explanation, LLM or deterministic fallback |
| POST | `/api/v1/decision-runs/{decision_run_id}/what-if` | `WhatIfRequest` | `200 T:WhatIfResponse` | in-memory recomputation; không persist hypothetical |

## 4. Request schemas

Pydantic write models bên dưới phần lớn `extra="forbid"`: field lạ tạo `422 validation_error`.

### 4.1 LLM and import

```text
MapSheetRequest
  profile: SheetProfile

SheetProfile
  file_name: string
  sheet_name: string
  header_row_zero_based: int >= 0
  row_count: int >= 0
  column_count: int >= 0
  columns: string[]
  dtypes: object<string,string>
  sample_rows?: object[] (max 8)

ConfirmRequest
  mappings: ConfirmedMapping[]

ConfirmedMapping
  sheet_id?: string
  profile_id?: UUID
  sheet_type: string = "unknown"
  column_mapping: object<string,string|null> = {}
  skip: bool = false
  constraint: phải có sheet_id hoặc profile_id
```

Multipart import:

```text
files: binary[] (required)
store_id: string (required)
forecast_date?: date
forecast_horizon: int 1..90 = 7
```

Upload limits lấy từ config: mặc định tối đa 10 files/request, 12 MB/file, 50 MB tổng, 30 sheets/file và 100,000 rows/sheet.

### 4.2 Catalog, menu and recipe

```text
IngredientCreate
  ingredient: string 1..255
  sku?: string
  base_unit: string 1..255
  active: bool = true
  expiry_tracking_mode?: required | not_required | unknown

IngredientPatch
  version: int >= 1
  ingredient?, sku?, base_unit?, active?, expiry_tracking_mode?
  constraint: ít nhất một update field

AliasBulkUpsert
  aliases: AliasUpsertItem[] (min 1)

AliasUpsertItem
  source_name: string 1..255
  canonical_name: string 1..255
  ingredient_id?: string

MenuProductCreate
  product: string 1..255
  sku?: string 1..64
  item_type: string = "single"
  selling_unit?: string
  price?: int >= 0 (service yêu cầu > 0 nếu có)
  status?: string
  active?: bool
  components: ComponentWrite[] = [] (max 20)

MenuProductPatch
  version: int >= 1
  product?, sku?, price?, status?, active?, selling_unit?, item_type?
  constraint: ít nhất một update field; item_type thực tế immutable

ComponentWrite
  component_product_id: string
  quantity: int > 0

ComponentsReplace
  version: int >= 1
  components: ComponentWrite[] (1..20)

RecipeWrite
  effective_from: date
  version: int >= 0
  yield_quantity: decimal > 0 = 1
  process_loss_rate: decimal 0 <= x < 1 = 0
  lines: RecipeLineWrite[] (min 1)

RecipeLineWrite
  ingredient_id: string
  quantity: decimal > 0
  unit: string
```

### 4.3 Inventory, history, supplier, settings and calendar

```text
CountIn
  counted_at: datetime
  lines: CountLine[] (min 1)
CountLine
  lot_id: string
  counted_quantity: decimal >= 0
  unit: string
  note?: string

AdjustmentIn
  occurred_at: datetime
  reference?: string
  lines: AdjustmentLine[] (min 1)
AdjustmentLine
  lot_id: string
  expected_version: int >= 1
  quantity_delta: decimal != 0 by service rule
  unit: string
  reason: waste | expired | damaged | correction_increase | correction_decrease | other
  note?: string (required by service when reason=other)

SalesBatchIn
  source: pos | manual | integration
  records: SalesRecordIn[] (min 1)
SalesRecordIn
  external_record_id: non-empty string
  date: date
  product_id: string
  quantity: decimal >= 0
  unit_price: int >= 0
  promotion: bool = false

PurchaseBatchIn
  source: supplier_invoice | manual | integration
  inventory_effect: must be "record_only"
  records: PurchaseRecordIn[] (min 1)
PurchaseRecordIn
  external_record_id?: string
  date: date
  ingredient_id: string
  supplier_id: string
  quantity: decimal > 0
  unit: string
  unit_cost: int >= 0
  expiry_date?: date
  supplier_lot_code?: string

SupplierTermIn
  ingredient_id: string
  supplier_id: string
  unit_cost: int >= 0
  moq: decimal >= 0
  pack_size: decimal > 0
  lead_time_days: int >= 0
  shelf_life_days?: int >= 0
  unit: string
  version?: int >= 1

SettingsIn
  monthly_budget: int >= 0
  forecast_horizon: int 1..90
  default_strategy: string
  version: int >= 1

CalendarIn
  items: CalendarItemIn[] (min 1)
CalendarItemIn
  date: date
  holiday: bool
  promotion: bool
  promotion_note?: string
```

### 4.4 Inventory constraints

```text
InventoryConstraintCreateIn
  ingredient_id?: string
  constraint_type: registered type
  value: decimal
  unit?: string
  currency?: string
  effective_date: date
  note?: string (max 500)

InventoryConstraintPatchIn
  expected_version: int >= 1
  value: decimal
  unit?: string
  currency?: string
  effective_date?: date
  note?: string (max 500)
  correction_mode?: replace_same_effective_date

InventoryConstraintDeactivateIn
  expected_version: int >= 1
  end_date: date
  note?: string (max 500)
```

`constraint_type` quyết định store/ingredient scope, dimension, allowed unit, min/max value và planner support. Client phải discover bằng `GET /business-constraint-types`; không hard-code từ tài liệu này.

### 4.5 Forecast and legacy plan

```text
ForecastTrainRequest
  store_id: safe path string 1..128
  cutoff_date: date
  model_version?: [A-Za-z0-9][A-Za-z0-9._-]{0,127}
  history_days?: int > 0

ForecastPredictRequest extends ForecastTrainRequest
  forecast_horizon: int >= 1

ForecastIn (compatibility)
  cutoff_date: date
  horizon_days: int 1..90
  quantiles: number[]
  scope: object<string,string[]>
  use_latest_calendar: bool = true

PlanIn (compatibility)
  forecast_run_id: string
  strategy: economy | balanced | safe | lean | protected
  budget_limit: int >= 0
  as_of_date: date
  include_open_purchase_orders: bool = true
```

### 4.6 Purchase orders

```text
POCreateIn
  plan_run_id: string
  lines: POCreateLine[] (min 1)
POCreateLine
  recommendation_id: string
  order_quantity_override?: decimal >= 0

POPatchIn
  version: int >= 1
  line_updates: POLineUpdate[] (min 1)
POLineUpdate
  po_line_id: string
  order_quantity: decimal > 0

POConfirmIn
  version: int >= 1
  confirmed_at: timezone-aware datetime

POReceiveIn
  version: int >= 1
  received_at: timezone-aware datetime
  delivery_reference: string
  lines: ReceiveLineIn[] (min 1)
ReceiveLineIn
  po_line_id: string
  lots: ReceiveLotIn[] (min 1)
ReceiveLotIn
  quantity: decimal > 0
  expiry_date?: date
  supplier_lot_code?: string
```

### 4.7 Planning and Decision Assistant

```text
ProcurementPlansRequest
  strategies: (lean | balanced | protected)[] = all three, min 1
  use_open_purchase_orders: bool = true
  use_latest_inventory: bool = true (false hiện bị reject)
  budget_override?: int >= 0

DecisionRunRequest
  forecast_run_id: string
  as_of_date: date
  horizon_days: int 1..90
  engine_mode?: legacy | deterministic | stochastic
  include_open_purchase_orders: bool = true
  budget_override?: int >= 0
  scenario_count?: int 1..1000
  random_seed?: int

ExplanationRequest
  language: vi | en = vi
  detail_level: simple | manager | technical = simple
  question?: string (max 2000)
  ingredient_id?: string 1..255

WhatIfRequest
  demand_multiplier?: number > 0
  supplier_delay_days?: int >= 0
  budget_limit?: int >= 0
  strategy?: lean | balanced | protected
```

Decision Run constraint quan trọng:

- `as_of_date` phải bằng forecast `cutoff_date`;
- `horizon_days` phải bằng forecast horizon;
- `legacy` không chạy Decision Package và trả 409;
- default config hiện là legacy, nên client mới nên truyền `engine_mode` rõ ràng.

## 5. Response schemas and runtime shapes

### 5.1 Import

```text
MappingSuggestion
  sheet_type, confidence, column_mapping, warnings[], errors[],
  source(rule|llm|rule_fallback), requires_review, raw_response?

ImportResponse
  import_id, status, store_id, forecast_date, forecast_horizon,
  sheets: ImportSheet[], profiles: ImportProfileResponse[],
  suggested_mappings: SuggestedMappingResponse[], warnings[], errors[],
  requires_review, created_at

StatusResponse
  import_id, status, mappings[], sheets[], profiles[],
  suggested_mappings[], requires_review

ProcessResponse
  import_id, status, validation_summary, processing_policy, issues[]
```

`GET /imports/{id}/result` chưa có response model. Body là persisted result gồm validation summary, normalized/business persistence metadata và issues tùy import state/policy; client không nên giả định field ngoài những gì use case hiện cần trước khi route được typed.

### 5.2 Catalog/menu/recipe

```text
IngredientResponse
  ingredient_id, store_id, ingredient, sku, base_unit, aliases[], active,
  expiry_tracking_mode, expiry_tracking_source, version, created_at, updated_at

AliasResponse
  alias_id, store_id, ingredient_id, alias, created_at

MenuProduct (untyped)
  product_id, store_id, sku, product, item_type, selling_unit, price,
  active, status, list_price, discount_rate, savings_amount, currency,
  components: MenuComponent[], version, created_at, updated_at

MenuComponent
  bundle_line_id, component_product_id, component_sku, component_product,
  sku, product, quantity, position, price, status

ActiveRecipeResponse
  product_id, store_id, recipe: RecipeVersionResponse|null
RecipeVersionResponse
  recipe_version_id, version, effective_from, effective_to,
  yield_quantity, process_loss_rate, content_hash, lines[], created_at
RecipeLineResponse
  recipe_line_id, ingredient_id, ingredient, quantity, unit
```

### 5.3 Operational views

```text
ImportSummary
  import_id, store_id, status, forecast_date, forecast_horizon,
  requires_review, file_count, profile_count, warning_count, error_count,
  created_at, completed_at, failed_at

InventoryLotView
  lot_id, ingredient_id, ingredient, sku, on_hand, usable_quantity,
  expiring_quantity, expired_quantity, unit, unit_cost, batch_code,
  received_date, received_date_status, expiry_date, supplier_id, supplier,
  status, snapshot_date, version

StoreSettingsView
  monthly_budget, reserved_budget, spent_budget, remaining_budget,
  forecast_horizon, default_strategy, safety_policy, version, updated_at

CalendarFeatureView
  date, weekday, weekend, holiday, promotion, promotion_note
```

Sales/usage/purchase/movement response hiện serialize gần như toàn bộ ORM columns trừ internal hash/profile fields. Đây không phải boundary ổn định; nên tạo DTO trước khi refactor models.

### 5.4 Completion write results

```text
InventoryCountResult
  inventory_count_id, adjustments: MovementDelta[], inventory: []

InventoryAdjustmentResult
  inventory_adjustment_id, occurred_at, movements: MovementDelta[]

MovementDelta
  movement_id?, lot_id, before_quantity, quantity_delta, after_quantity, version

SalesBatchResult
  batch_id, created_count, unchanged_count,
  records[{external_record_id,sales_record_id,status}],
  usage_rebuild{status,warning_count}, warnings[]

PurchaseBatchResult
  batch_id, created_count, unchanged_count,
  records[{external_record_id,purchase_record_id,status}], inventory_applied:false

CalendarWriteResult
  created_count, updated_count, unchanged_count, items: CalendarFeatureView[]
```

### 5.5 Supplier and inventory constraints

```text
SupplierTerm
  constraint_id, ingredient_id, supplier_id, supplier, unit_cost,
  moq, pack_size, order_unit, available_delivery_days, lead_time_days,
  shelf_life_days, unit, version, active

SupplierTermList
  items: SupplierTerm[], page, page_size, total

InventoryConstraintList
  store_id, as_of_date, items: InventoryConstraintOut[]

InventoryConstraintOut
  constraint_id, ingredient_id, ingredient_name, constraint_type, value,
  unit, currency, effective_date, end_date, version, active

InventoryConstraintWriteOut
  constraint: InventoryConstraintWriteItem
  history: InventoryConstraintHistoryItem[]
```

### 5.6 Purchase order

```text
PurchaseOrder
  po_id, supplier_id, supplier, plan_run_id, order_date, delivery_date,
  strategy, status, lines: PurchaseOrderLine[], total, budget_after,
  version, confirmed_at, received_at

PurchaseOrderLine
  po_line_id, ingredient_id, order_quantity, received_quantity, unit,
  unit_cost, line_total, shelf_life_days, projected_expiry_date
```

State transitions:

```text
draft --confirm--> ordered --receive(partial)--> partially_received
ordered/partially_received --receive(complete)--> received
```

Patch chỉ hợp lệ ở `draft`. Confirm reserve budget; receive chuyển reserved sang spent và tạo receipt/lot/movement.

### 5.7 Forecast and legacy plan

```text
ForecastPredictionResponse
  product_id, product_name, target_date, horizon,
  p25, p50, p75, interval_lower, interval_upper,
  baseline_p50, calibration_source, warnings[]

ForecastResponse
  forecast_run_id, store_id, forecast_date, forecast_horizon,
  model_version, status, predictions[], warnings[], created_at, completed_at

ForecastTrainingResponse
  store_id, model_version, status, trained_at,
  history_start, history_end, metrics, warnings[]

LegacyForecastMetadataResponse
  forecast_run_id, store_id, status, engine_status, cutoff_date,
  horizon_days, model_version, warnings[], failure_code, failure_message,
  created_at, completed_at, result_url

LegacyForecastResultResponse extends metadata
  forecast_date?, forecast_horizon?, predictions[]

LegacyPlanMetadataResponse
  plan_run_id, store_id, forecast_run_id, procurement_plan_run_id,
  status, engine_status, strategy, planning_strategy, budget_limit,
  as_of_date, include_open_purchase_orders, created_at, completed_at,
  result_url, warnings[], failure_code, failure_message

LegacyPlanResultResponse extends metadata
  is_feasible, is_recommended, total_purchase_cost,
  projected_shortage_quantity, projected_waste_quantity, fill_rate,
  budget_used, budget_remaining, constraint_violations[], plan_lines[],
  budget_trace, storage_capacity_trace, shelf_life_trace, simulation_summary[]
```

### 5.8 Ingredient demand and procurement planning

```text
IngredientDemandRun (untyped)
  ingredient_demand_run_id, forecast_run_id, store_id, status,
  warnings[], failure_code, failure_message, created_at, completed_at,
  predictions: IngredientDemandPrediction[]

IngredientDemandPrediction
  ingredient_id, ingredient_name, target_date, horizon, unit,
  p25, p50, p75, source_product_count, contributions[], warnings[]

ProcurementPlanRun (untyped)
  procurement_plan_run_id, forecast_run_id, ingredient_demand_run_id,
  store_id, status, recommended_strategy, warnings[], failure_code,
  failure_message, created_at, completed_at, plans: ProcurementPlan[]

ProcurementPlan
  procurement_plan_id, strategy, is_feasible, is_recommended,
  total_purchase_cost, projected_shortage_quantity, projected_waste_quantity,
  fill_rate, budget_used, metrics, warnings[], daily_projections[], lines[]
```

Planning line hiện gồm supplier/term/order/arrival/raw required/order quantity/rounding/unit/pack/cost/MOQ/lead time/reason codes/warnings. Vì route chưa typed, consumer nên pin contract test nếu dùng nested technical fields.

### 5.9 Decision Package

Raw `DecisionPackage` chưa có Pydantic response model. Current top-level shape:

```text
decision_run_id: string
store_id: string
as_of_date: date
horizon_days: int
status: completed | completed_with_no_feasible_recommendation
engine_mode: deterministic | stochastic
recommended_strategy?: lean | balanced | protected
business_metrics: object
recommended_plan: {items: object[]}
ingredient_demand: object[]
inventory_risk: object
strategies: object<string,StrategyPackage>
strategy_selection: object
stress_tests: object|object[]
critic: object
reason_codes: object[]
warnings: string[]
technical_metrics: object
assistant?: {
  overall_summary?: AssistantSummary,
  ingredient_synthesis?: IngredientSynthesis[],
  ingredient_synthesis_diagnostics?: object
}
```

`POST` và raw `GET` đều trả shape này. `assistant` có thể phụ thuộc thời điểm enrichment/old run; frontend chính nên dùng `/brief`.

### 5.10 Decision Brief

```text
DecisionBriefFacts
  decision_run_id, store_id, status, forecast: ForecastBrief,
  recommendation: RecommendationBrief,
  procurement_rows: ProcurementRowBrief[],
  ingredient_demand: IngredientDemandBrief[],
  ingredient_demand_summary: IngredientDemandSummaryBrief[],
  risk: RiskBrief, critic: CriticBrief,
  risk_details: RiskDetail[], ingredient_synthesis: IngredientSynthesis[],
  presented_warnings: PresentedWarning[],
  strategy_comparison?: StrategyComparisonBrief,
  strategy_evaluations: BriefStrategyEvaluation[],
  evidence: EvidenceBrief[], data_availability,
  assistant_summary?: AssistantSummary, generated_at
```

Key nested DTOs:

```text
RecommendationBrief
  available, strategy?, summary?, total_purchase_cost?, expected_fill_rate?

ProcurementRowBrief
  ingredient_id, ingredient_name?, supplier_id?, supplier_name?, quantity,
  unit?, pack_count?, pack_size?, order_date?, arrival_date?,
  purchase_cost?, reason_codes[]

IngredientDemandBrief
  ingredient_id, ingredient_name?, unit?, target_date,
  p25?, p50?, p75?, contributions[]

RiskBrief
  stockout_probability?, expected_fill_rate?, shortage_quantity?, waste_quantity?

AssistantSummary
  headline, summary, key_points[], warning_summary?,
  source(llm|deterministic_fallback), grounded, raw_response?, llm_diagnostics?

IngredientSynthesis
  ingredient_id, ingredient_name?, unit?, importance(normal|watch|critical),
  source(rule_based|llm|deterministic_fallback), headline, summary, evidence_ids[]
```

### 5.11 Explanation and what-if

```text
DecisionExplanationResponse
  source, language, detail_level, summary, why_this_plan[], main_risks[],
  tradeoffs[], important_assumptions[], decision_run_id, answer, intent,
  entities, claims: ExplanationClaim[], citations: Citation[], grounded,
  provider, authority?, raw_response?, llm_diagnostics?

ExplanationClaim
  type, value, unit?, evidence_ids[]
Citation
  evidence_id, label, source_type

WhatIfResponse
  decision_run_id,
  baseline: DecisionBriefFacts,
  hypothetical: DecisionBriefFacts,
  mutations: WhatIfRequest,
  comparison: WhatIfComparison,
  mutation_facts?: WhatIfMutationFacts,
  grounded_explanation?: DecisionExplanationResponse,
  generated_at

WhatIfComparison
  recommendation_changed, baseline_strategy?, hypothetical_strategy?,
  purchase_cost_delta?, expected_fill_rate_delta?,
  expected_fill_rate_percentage_point_delta?, stockout_probability_delta?,
  shortage_quantity_delta?, waste_quantity_delta?, feasibility_changed,
  strategy_change?, order_changes[], warnings_added[], warnings_removed[],
  hard_violations_added[], hard_violations_removed[],
  new_issues[], resolved_issues[], new_risks[], resolved_risks[]
```

What-if hypothetical ID có dạng `what-if:{baseline_decision_run_id}` và `authority="HYPOTHETICAL"`; không thể dùng ID này để GET một persisted Decision Run.

## 6. Contract maturity / refactor checklist

| Area | Current status | Việc cần khóa trước refactor |
|---|---|---|
| Imports | request/most response typed | type `GET /result`; document state machine |
| Ingredient/alias/recipe | typed | giữ optimistic version/idempotency tests |
| Product/menu | request typed, response untyped | tạo `MenuProductResponse`, `Page` DTO |
| Operational reads | untyped ORM-derived DTO | tách public response khỏi ORM columns |
| Completion writes/PO | request typed, response untyped | tạo response DTO và state-transition contract |
| Forecast | typed | hợp nhất legacy/deprecated/canonical lifecycle |
| Ingredient demand/plans | untyped nested dict | tạo run/prediction/plan/line DTO |
| Raw Decision Package | `dict[str,Any]` | thêm schema version + typed package |
| Brief/explanation/what-if | typed | bỏ raw provider details khỏi stable frontend surface nếu không cần |
| Errors | runtime envelope typed trong code nhưng OpenAPI lệch | đăng ký common error responses cho mọi operation |

## 7. Source of truth map

- Route/method/status/input: `app/api/*.py`
- Request/typed response: `app/schemas/*.py`, model classes trong `app/api/completion.py`, `app/decision_intelligence/contracts.py`
- Actual untyped response shape: `app/services/*.py`
- Error envelope: `app/main.py`
- Auth/dependencies: `app/dependencies.py`
- Runtime schema: `GET /openapi.json`
- Decision Assistant stable details: `docs/decision_assistant_api_contract.md`
- Architecture context: `docs/CURRENT_ARCHITECTURE.md`

# Checkpoint 0 — ShelfCash Backend Codebase Audit & Gap Analysis

Audit date: 2026-07-28  
Scope: repository state at Checkpoint 0  
Baseline test command: `pytest -q`

## 1. Executive Summary

ShelfCash hiện là một FastAPI MVP tập trung vào ingestion Excel và semantic column
mapping. Code đã có phân lớp thực dụng cho router, Pydantic schema, import service,
repository interface, SQLite repository, ingestion core và LLM provider. Tám route
đang tồn tại: health, LLM health/map-sheet và năm route import. Baseline tests pass
`9/9`.

Luồng cần giữ nguyên là:

```text
FastAPI router
→ ImportService
→ Excel reader / SheetProfile
→ rule mapper
→ optional Qwen provider
→ mapping confirmation
→ normalizer / record validator
→ SQLite import payload + canonical JSON result
```

Persistence hiện tại chưa phải persistence nghiệp vụ theo API contract. SQLite chỉ
có một ORM table `imports`; toàn bộ state, sheet profile, rows, mapping và canonical
result được serialize vào một cột JSON text. Upload và result còn được lưu trên
filesystem. Import job tồn tại sau restart nếu dùng cùng database/filesystem, nhưng
ingredients, inventory, recipes, histories, forecast, plan và PO chưa có bảng hoặc
API riêng.

Các blocker lớn nhất trước khi mở rộng API là:

1. Chưa có database foundation/migration/session boundary cho dữ liệu nghiệp vụ.
2. Chưa có store isolation, transaction end-to-end hoặc idempotency.
3. Public import/error contract đang khác contract mới và cần compatibility-first
   migration, không được đổi response âm thầm.

Kết luận: không viết lại import/Qwen. Checkpoint tiếp theo nên xây database
foundation và migration theo kiểu additive, giữ nguyên eight-route behavior, sau
đó mới chuyển import persistence và triển khai API nghiệp vụ.

## 2. Repository Structure

Kiến trúc thực tế:

```text
app/
  main.py                     app factory, lifespan, router registration, handlers
  config.py                   pydantic-settings và runtime limits
  dependencies.py             app-state DI và X-ShelfCash-Key
  api/
    health.py                 backend health route
    llm.py                    LLM health và map-sheet routes
    imports.py                upload/status/confirm/process/result routes
  schemas/
    llm.py                    SheetProfile và MappingSuggestion
    imports.py                import request/response schemas
    canonical.py              canonical ingestion result envelope
  services/
    import_service.py         import workflow và filesystem writes
  repositories/
    imports.py                ImportRepository abstraction
    sqlite_imports.py         SQLAlchemy/SQLite implementation và ImportModel
  core/
    canonical_schemas.py      canonical sheet types, fields, core fields
    excel_reader.py           workbook parsing, limits, header detection
    sheet_profiler.py         compatibility re-export
    rule_mapper.py            sheet/column rules và mapping validator
    ingestion_pipeline.py     rule/LLM selection, confirm, normalize, validate
    normalizer.py             date/number/bool/unit normalization
    validator.py              row-level core-field validation
  llm/
    base.py                   provider interface
    disabled.py               CPU/test fallback
    factory.py                provider selection
    local_qwen.py             Qwen lifecycle, inference và output validation
tests/
  conftest.py                 isolated tmp SQLite/filesystem, LLM disabled
  test_health.py              backend và disabled-LLM health
  test_excel_upload.py        upload and file validation
  test_manual_confirmation.py confirm/process/result and mapping validation
  test_rule_mapper.py         deterministic mapping
  fixtures/                   generated in-memory fake workbook
scripts/
  create_fake_excel.py        writes a local fake workbook
  smoke_test_api.py           manual health/upload smoke test
runtime/
  uploads/                    uploaded workbooks at runtime
  results/                    canonical JSON results at runtime
docs/
  ShelfCash_API_Contract_v1.md
  CODEX_MASTER_INSTRUCTIONS.md
README.md                     architecture, local/Kaggle usage and API examples
requirements.txt              runtime + tests
requirements-kaggle.txt       Qwen GPU extras
pyproject.toml                package metadata and pytest configuration
Makefile                      install/run/test/fake/smoke commands
.env.example                  documented runtime settings
```

Không tồn tại trong repository: `app/api/v1/`, `app/routers/`, `app/models/`
riêng, `app/db/`, `app/utils/`, Alembic, migration files, seed scripts,
Dockerfile, compose file, notebook/Kaggle notebook hoặc Node.js proxy source.
`ImportModel` hiện nằm trực tiếp trong `app/repositories/sqlite_imports.py`.

## 3. Current Architecture

### Application lifecycle và DI

`app.main.create_app()` tạo FastAPI app. Lifespan tạo upload/result directories,
khởi tạo `SQLiteImportRepository`, provider qua factory, load Qwen nếu cấu hình
`local_qwen`, tạo `IngestionPipeline` và `ImportService`, rồi đặt chúng vào
`app.state`. `get_service()` và `get_llm_provider()` đọc từ `app.state`.

Đây là DI đủ dùng cho MVP/tests nhưng chưa có database session dependency. Mỗi
repository method tự mở `Session`, commit, đóng session. Không có unit-of-work hoặc
transaction chia sẻ giữa repositories/services.

### Layering assessment

| Layer | Hiện trạng | Assessment |
|---|---|---|
| API | Router mỏng, nhưng tự chuyển `KeyError`/`ValueError` | Khá phù hợp; cần domain exceptions |
| Schemas | Pydantic request/response và internal mapping schemas | Phù hợp cho import; thiếu business schemas |
| Services | Một `ImportService` điều phối toàn workflow | Nên giữ, nhưng tách transaction/persistence boundary |
| Repositories | Interface + SQLite repository | Nên giữ abstraction; implementation quá hẹp |
| Models/DB | Một ORM model nằm trong repository; không session manager | Không đạt target architecture |
| Core | Reader, mapper, normalizer, validator tách riêng | Nên giữ và test thêm |
| LLM | Provider interface và lazy heavy imports | Phù hợp master instructions |
| Filesystem | Upload/result do service ghi trực tiếp | Legacy compatibility, không phải DB source of truth |

Import router/service đang có business flow hợp lý để tiến hóa. Không có lý do
kiến trúc để thay framework, thay Qwen hoặc viết lại pipeline.

## 4. Endpoint Inventory

Có đúng **8 FastAPI routes** do ứng dụng định nghĩa.

| Method | Full path | Router file | Handler | Request schema | Response schema | Auth dependency | Service/provider gọi | Status | Notes |
|---|---|---|---|---|---|---|---|---|---|
| GET | `/health` | `app/api/health.py` | `health` | None | implicit dict | None | None | partial | Tested; contract example có `version`, code chưa có |
| GET | `/api/v1/llm/health` | `app/api/llm.py` | `llm_health` | None | implicit dict | None | injected LLM provider `health()` | implemented | Tested với disabled provider; intentionally public |
| POST | `/api/v1/llm/map-sheet` | `app/api/llm.py` | `map_sheet` | `MapSheetRequest` | `MappingSuggestion` | `require_api_key` | rule mapper + LLM provider | missing test | Implemented flow; không có endpoint test trực tiếp |
| POST | `/api/v1/imports` | `app/api/imports.py` | `create_import` | multipart: files, store_id, optional date/horizon | `ImportResponse` | router-level `require_api_key` | `ImportService.create_import` | partial | Tested; response/state khác contract mới |
| GET | `/api/v1/imports/{import_id}` | `app/api/imports.py` | `get_import` | UUID path | `StatusResponse` | router-level | `ImportService.get` | partial | Không có direct test; trả `mappings` |
| POST | `/api/v1/imports/{import_id}/confirm` | `app/api/imports.py` | `confirm_import` | `ConfirmRequest` | `StatusResponse` | router-level | `ImportService.confirm` | partial | Tested; dùng `sheet_id`, không phải `profile_id` |
| POST | `/api/v1/imports/{import_id}/process` | `app/api/imports.py` | `process_import` | UUID path, no body | `ProcessResponse` | router-level | `ImportService.process` | partial | Tested; sync, không transaction/idempotency |
| GET | `/api/v1/imports/{import_id}/result` | `app/api/imports.py` | `get_result` | UUID path | implicit canonical dict | router-level | `ImportService.result` | partial | Tested after process; 409 differs contract readiness semantics |

### Import compatibility differences

| Concern | Current code | API contract | Classification |
|---|---|---|---|
| Create response collection | `sheets[]` containing nested `profile` + `mapping` | `profiles[]` + `suggested_mappings[]` | Existing but different |
| Status response collection | `mappings[]`, each still shaped as `ImportSheet` | Contract-oriented profile/mapping records | Existing but different |
| Mapping identifier | opaque `sheet_id` built from stored filename and sheet ID | UUID `profile_id` | Existing but different |
| Initial status | `awaiting_review` | `mapping_required` | Existing but different |
| Confirm status | always sets `confirmed`, even if unsubmitted sheets still require review | confirmed transition after valid mappings | Partial |
| Process status | directly becomes `processed` | `processing → completed \| failed` | Existing but different |
| Result-not-ready | HTTP 409 `result_not_ready` | contract lists `IMPORT_NOT_READY`, commonly 425 | Existing but different |
| Health body | status + service | contract example also has version | Additive gap |

Theo master instructions, `sheets`, `mappings`, `sheet_id` và các status hiện tại
phải được coi là compatibility surface. Khi thêm contract fields, chúng phải được
sinh từ cùng source và có contract tests.

### Contract endpoint coverage

Contract liệt kê 50 routes. Tám route trên tồn tại; 42 route còn lại chưa có.
Không tìm thấy placeholder. Toàn bộ store bootstrap/dashboard, inventory,
catalog, recipe, history, supplier/configuration, forecast, plan và PO endpoints
đều **chưa có**.

## 5. Database and Persistence Audit

### Direct answers

1. **Session/connection manager:** chưa có shared manager; repository tự tạo engine
   một lần và mở `Session` riêng trong từng method.
2. **ORM models:** có một `ImportModel`, đặt trong SQLite repository.
3. **Migration system:** không có.
4. **Alembic:** không có dependency/configuration/directory.
5. **Startup `create_all`:** có, trong `SQLiteImportRepository.__init__()`.
6. **Test database:** SQLite file riêng trong pytest `tmp_path`; uploads/results
   cũng ở `tmp_path`.
7. **Import sau restart:** có, nếu SQLite file còn và dùng cùng URL. Payload chứa
   profiles/rows/mappings/result. Upload/result file cũng phụ thuộc filesystem còn.
8. **Upload location:** `{UPLOAD_DIR}/{import_id}/{uuid}_{sanitized_filename}`.
9. **Canonical records vào DB:** không vào canonical business tables; canonical
   dict được nhúng lại trong `imports.payload` và ghi thêm JSON result file.
10. **Forecast/plan/PO persistence:** không có.
11. **Transaction:** mỗi create/update row import có SQLAlchemy transaction riêng;
    không có transaction bao trùm filesystem, process hoặc nhiều business tables.
12. **Unique/idempotency:** chỉ primary key `imports.id`; không checksum, row hash,
    idempotency key hoặc business unique constraints.
13. **Store isolation:** không có authorization/isolation; `store_id` chỉ nằm trong
    JSON payload và request.

### Resource storage matrix

| Data/resource | Current storage | Sau restart | Transaction | Idempotency | Contract target | Severity |
|---|---|---:|---|---|---|---|
| Store | string trong import payload | Chỉ cùng import | Row-level only | No | `stores` | Critical |
| Settings | business_constraints canonical JSON only | Yes inside import row/file | No workflow tx | No | `store_settings` | High |
| Ingredients | names trong canonical rows | Yes inside import row/file | No workflow tx | No | `ingredients` | Critical |
| Aliases | None | No | No | No | `ingredient_aliases` | High |
| Products | names trong canonical rows | Yes inside import row/file | No workflow tx | No | `products` | Critical |
| Recipe versions | canonical recipe rows only | Yes inside import row/file | No workflow tx | No | `recipe_versions`, `recipe_lines` | Critical |
| Inventory lots | canonical inventory rows only | Yes inside import row/file | No workflow tx | No | `inventory_lots` | Critical |
| Inventory movements | None | No | No | No | `inventory_movements` | Critical |
| Sales history | canonical JSON only | Yes inside import row/file | No workflow tx | No | `sales_daily` | Critical |
| Usage history | canonical JSON only | Yes inside import row/file | No workflow tx | No | `usage_daily` | Critical |
| Purchase history | canonical JSON only | Yes inside import row/file | No workflow tx | No | `purchase_receipts` | Critical |
| Supplier constraints | canonical JSON only | Yes inside import row/file | No workflow tx | No | suppliers + terms | Critical |
| Calendar features | canonical JSON only | Yes inside import row/file | No workflow tx | No | `calendar_features` | High |
| Import jobs | SQLite `imports` row | Yes | Per-row commit | PK only | `import_jobs` | Medium |
| Import files | metadata implicit in payload; binary filesystem | Yes if FS remains | No cross-store tx | No checksum | `import_files` + object/FS storage | High |
| Import profiles | nested JSON in `imports.payload` | Yes | Same row update | No | `import_sheet_profiles` | High |
| Import mappings | nested JSON in `imports.payload` | Yes | Same row update | No | `import_mappings` | High |
| Import issues | warning/error arrays and validation summary in payload | Yes | Same row update | No | `import_issues` | High |
| Forecast runs | None | No | No | No | `forecast_runs` | High |
| Forecast points | None | No | No | No | `forecast_points` | High |
| Plan runs | None | No | No | No | `plan_runs` | High |
| Recommendations | None | No | No | No | `recommendations` | High |
| Purchase orders | None | No | No | No | PO header/lines | Critical |
| Audit logs | None | No | No | No | `audit_logs` | High |

`ImportModel.payload` là JSON serialized text chứ không phải JSON database type.
Repository không dùng raw SQL. Không có in-memory production dictionary hoặc
module-level business state; app-state chỉ giữ service/provider objects.

## 6. Import Pipeline Trace

### End-to-end trace

1. `POST /api/v1/imports` nhận multipart trong `app/api/imports.py`.
2. Router gọi `ImportService.create_import()`.
3. Service kiểm tra tối đa số file (`10` mặc định), đọc tối đa
   `max_file_size_mb * 1024² + 1` và từ chối khi quá `20 MB` mặc định.
4. `sanitize_filename()` dùng `Path(filename).name`; service thêm UUID prefix.
5. `read_workbook()` chấp nhận `.xlsx`, `.xls`, `.xlsm`; chọn `xlrd` cho `.xls`,
   `openpyxl` cho loại còn lại.
6. Workbook bị giới hạn `30` sheets và `100,000` data rows/sheet mặc định.
7. `detect_header()` scan tối đa 15 raw rows; score =
   non-empty count + `2 * string_ratio` + unique ratio; row score cao nhất thắng.
8. Reader tạo `SheetProfile`: file/sheet name, zero-based header row, dimensions,
   columns, inferred dtypes và tối đa `8` sample rows.
9. `IngestionPipeline.suggest()` luôn gọi `map_sheet_rules()` trước.
10. Rule mapper đoán sheet type bằng keyword trong sheet name; map columns bằng
    normalized aliases; confidence kết hợp 55% sheet keyword và 45% column match.
11. Nếu rule confidence `>= settings.rule_confidence_threshold` (mặc định `0.82`),
    trả rule ngay. Nếu thấp và provider available, gọi provider. Nếu provider
    unavailable, trả rule fallback và bắt buộc review.
12. Qwen output đi qua `LocalQwenProvider._validate_result()`, kiểm tra đúng toàn bộ
    source keys và gọi `validate_mapping()` cho target fields/core fields.
13. Service giữ profile, mapping và **toàn bộ parsed rows** trong import payload;
    lưu raw upload vào filesystem và payload vào SQLite.
14. `_public_import()` bỏ `rows`/stored filename khỏi response, trả `sheets`.

### Canonical sheet types

`inventory`, `sales_history`, `usage_history`, `recipes`, `purchase_history`,
`supplier_constraints`, `calendar_features`, `business_constraints`, `unknown`.
Canonical fields/core fields nằm trong `app/core/canonical_schemas.py`; đây là
mapping schema cho ingestion, chưa phải public business ORM schema.

### Confirmation

`ImportService.confirm()` tạo lookup từ submitted `sheet_id`. Mappings không được
submit bị giữ nguyên. Với mapping được submit, pipeline:

- điền đủ mọi profile column, column thiếu map thành `null`;
- gọi `validate_mapping(sheet_type, profile.columns, complete_mapping)`;
- từ chối errors, giữ warnings;
- tạo `MappingSuggestion` confidence `1.0`, source `rule`;
- tính `requires_review = bool(warnings)`.

Sau đó service tính lại aggregate `requires_review` từ mọi sheet, nhưng vẫn đặt
status `confirmed` bất kể aggregate còn true.

### Processing/result

`process()` không kiểm tra state `confirmed`. Với từng sheet, pipeline normalize
mapped values, thêm `_source_file`, `_source_sheet`, `_source_excel_row`, rồi
`validate_records()` chỉ đếm missing core fields. Invalid rows vẫn được giữ trong
canonical output. Service tạo `CanonicalResult`, ghi
`runtime/results/{import_id}.json`, rồi update SQLite payload thành status
`processed` và nhúng toàn canonical result. `GET /result` đọc result từ SQLite
payload, không đọc file JSON.

### Repeat/process failure behavior

- Process hai lần ghi đè cùng JSON result path và cùng `imports` row; với code hiện
  tại không tạo duplicate **business rows** vì chưa có business tables.
- Khi business persistence được thêm, flow hiện tại không có checksum/row hash/
  idempotency guard nên sẽ có nguy cơ duplicate nghiêm trọng.
- Nếu lỗi sau khi result file đã ghi nhưng trước SQLite update, filesystem và DB
  bất nhất. Nếu lỗi giữa nhiều sheet, chưa có business writes để rollback, nhưng
  cũng chưa có transaction workflow.

### Mapping warning risk analysis

- `validate_mapping()` đúng khi kiểm tra target từ `mapping.values()`: **Confirmed
  correct**.
- Manual confirm tính warning từ `complete_mapping` mới, không dùng mapping cũ:
  **Confirmed correct**.
- Qwen `_validate_result()` dùng Qwen mapping mới, không dùng rule mapping cũ:
  **Confirmed correct**.
- Qwen giữ `raw["warnings"]` do model trả rồi nối validator warnings. Nếu model echo
  warning cũ từ `rule_suggestion` dù final mapping đã đủ, warning lỗi thời và
  `requires_review` có thể còn: **Likely; needs runtime reproduction**.
- Rule warnings không được code nối trực tiếp sau Qwen; chúng chỉ có thể quay lại
  qua model output/prompt: **Direct code bug not found**.
- Rule threshold và rule mapper hardcode `0.82` cho initial `requires_review`,
  trong khi pipeline selection dùng configurable threshold. Nếu threshold config
  khác `0.82`, selection và flag có thể không nhất quán: **Confirmed design
  inconsistency**.
- Qwen confidence không được dùng để tính final `requires_review`; chỉ errors và
  “Missing core fields” warnings được dùng: **Confirmed**, cần policy quyết định.

## 7. Qwen Provider Audit

| Check | Finding | Master compliance |
|---|---|---|
| Model ID | `Settings.qwen_model_id`, env `QWEN_MODEL_ID`, default `Qwen/Qwen3-4B` | Compliant |
| Load timing | Factory lazy-imports provider; model loads in FastAPI lifespan, not module import | Compliant |
| 4-bit | Configurable, default true | Compliant |
| BitsAndBytesConfig | NF4, double quant, float16 compute | Present |
| device map | `device_map="auto"` | Present |
| Concurrency guard | `asyncio.Semaphore(1)` | Present |
| Blocking isolation | load and generation use `asyncio.to_thread` | Present |
| Timeout | `asyncio.wait_for`, default 180 seconds | Present |
| Thinking | `enable_thinking=False` | Present |
| Determinism | `do_sample=False` | Present |
| Decode | slices prompt tokens, decodes generated tokens only | Correct |
| JSON parse | tokenizer text → `json_repair.repair_json()` → `json.loads()` | Present |
| Output validation | exact source-key set + canonical mapping validation + Pydantic | Present |
| Tests/provider | tests set `llm_provider="disabled"`; no Qwen mock object | Safe; no model download |
| Health | reports provider/model/available/loaded/CUDA/GPU/quantization | Mostly accurate after load |
| Startup failure | load exception propagates and fails lifespan startup | Clear failure, no domain/log wrapper |
| Provider failure | any mapping exception silently falls back to rule with warning | Resilient but broad catch hides cause |
| Input scope | full `SheetProfile`, at most 8 samples; no workbook rows beyond samples | Compliant |

`GET /api/v1/llm/health` is not authenticated, consistent with README and current
compatibility. No test exercises real Qwen output validation, timeout, fallback or
health in loaded/unloaded states.

## 8. Schema and Error Contract Audit

### Pydantic schema inventory

| Schema | Role | Important validation |
|---|---|---|
| `SheetProfile` | Internal/request LLM profile | nonnegative dimensions/header; samples max 8 |
| `MappingSuggestion` | LLM/rule response and internal mapping | sheet type Literal, confidence 0..1, target field validator |
| `MapSheetRequest` | LLM request | required profile |
| `ImportSheet` | Nested public import item | profile + mapping |
| `ImportResponse` | Create import response | UUID, date, datetime, sheets |
| `ConfirmedMapping` | Confirm input item | `sheet_id`, unconstrained string `sheet_type`, mapping |
| `ConfirmRequest` | Confirm request | mappings list |
| `StatusResponse` | Get/confirm response | UUID, status string, mappings |
| `ProcessResponse` | Process response | UUID, status, free-form summary |
| `CanonicalResult` | Internal/result response | typed envelope, row contents are free-form dicts |

Không có ORM/public schema mixing trực tiếp; ORM model không được return. Tuy nhiên
nhiều field là free-form `str`, `dict[str, Any]` và extra-field policy không được
khai báo, nên Pydantic mặc định ignore extra input. UUID handling tốt ở path/public
IDs; `sheet_id` không phải UUID. `forecast_date` bị đổi từ `date` ở public create
response thành string trong canonical result. Timestamps do service dùng UTC
timezone; chưa áp store timezone. JSON fields dùng snake_case.

Unit normalizer hiện canonicalize sang tiếng Anh (`kilogram`, `gram`, `liter`,
`milliliter`, `piece`) và còn giữ `cup`, `box`, `case`, `bottle`, `package`; khác
contract MVP (`kg`, `g`, `lít`, `ml`, `cái`). Không có conversion giữa dimensions,
điểm này đúng.

### Error handling

`app/main.py` có handlers cho:

- `ExcelIngestionError` → 400, `{code,message,details}`;
- `HTTPException` → giữ detail có ba field hoặc tạo `http_error`;
- Pydantic `ValidationError` → 422;
- FastAPI `RequestValidationError` → 422.

Gaps:

- Mọi envelope đều thiếu `request_id`.
- Error codes hiện lowercase, contract chuẩn uppercase.
- `ValueError` trong confirm bị trả nguyên `str(exc)`; có thể lộ internal validation
  wording, dù chưa phải stack trace.
- Router bắt rộng `KeyError` ở confirm/process/result. Một `KeyError` nội bộ không
  liên quan missing import cũng bị đổi thành 404: **Confirmed**.
- Không có `ShelfCashError` hierarchy hoặc domain handler.
- Unknown exceptions dùng FastAPI default 500; production không trả stack trace
  nếu debug false, nhưng không đảm bảo contract envelope.
- 401 có handler nhưng chỉ được kích hoạt khi configured API key khác rỗng.
- Không có explicit behavior/tests cho 403, contract-style 409, 413, 425, 500,
  503. File quá lớn hiện trả 400, không phải 413.
- Result-not-ready trả 409; model-not-ready không có.

## 9. Security and Store Isolation Audit

| Area | Finding | Assessment |
|---|---|---|
| API key validation | `require_api_key()` dùng `secrets.compare_digest` | Good |
| API key disabled | nếu configured value rỗng, protected routes trở thành public | Development convenience; production risk |
| API key logging | không có request logging; key không được log trong code | No leak found |
| Store authorization | không có identity-to-store permission model | Critical gap |
| Store-filtered lookup | import lookup chỉ bằng `import_id`; không filter store | Critical cross-store risk once IDs are known |
| Filename sanitation | `Path(filename).name` + generated UUID prefix | Path traversal mitigated |
| Extension validation | allowlist `.xlsx/.xls/.xlsm` | Present |
| File content validation | parser attempts workbook open | Better than extension only |
| Content-Type validation | không kiểm tra multipart file content type | Gap |
| File size | bounded read and explicit limit | Present |
| Workbook limits | sheet and row limits | Present |
| CORS | configurable origins, credentials true, all methods/headers | Broad methods/headers but origin-limited |
| CORS parsing | validator accepts comma-separated string | `.env.example` CSV is supported; claimed JSON-only bug not found |
| Hardcoded secret | none found; `.env.example` key blank | Good |
| Node proxy | contract/README only; source/allowlist không nằm repo | Cannot audit implementation |
| Quick Tunnel | README warns runtime/URL instability | DNS issue not provable from code; operational risk only |

`SettingsConfigDict(case_sensitive=False)` reads `.env`; the CSV CORS validator
splits comma-separated values correctly. There is no explicit production check
that refuses startup when `SHELFCASH_API_KEY` is blank.

## 10. Test Suite Audit

Command:

```text
pytest -q
```

Result:

```text
9 passed, 0 failed, 0 skipped, 1 warning in 0.77s
```

Warning: Starlette deprecation warning from the installed FastAPI TestClient
integration with `httpx`; no application test failed. No environment variable was
required. Tests explicitly construct `Settings(llm_provider="disabled")`, use
SQLite under `tmp_path`, and do not access network/GPU.

Coverage tooling is not configured, so no coverage percentage is available.
`ruff`, `mypy`, `black` and pre-commit are not configured and were not run.

Existing coverage:

- health and disabled LLM health;
- successful workbook upload;
- invalid extension and oversized file;
- manual confirm → process → result;
- changed sheet type revalidation;
- invalid target field rejection;
- one deterministic rule mapping.

Important missing tests:

- `/llm/map-sheet`, authentication enabled/invalid key;
- GET import and all not-found/result-not-ready paths;
- Qwen validated output/fallback/timeout without loading model;
- persistence across application restart;
- repeated process/idempotency;
- filesystem/DB partial failure;
- all domain error envelopes;
- two-store isolation;
- transaction rollback, version conflict, invalid transition;
- malicious/corrupt workbook, content type, max files/sheets/rows;
- CORS behavior and production config safety.

## 11. API Contract Gap Matrix

Severity totals: **Critical 6, High 16, Medium 7, Low 1**.

| Contract area | Required capability | Current implementation | Gap | Severity | Dependency | Recommended checkpoint |
|---|---|---|---|---|---|---|
| Backend foundation | DB/session/model layers and stable DI | App-state DI; import repository only | No shared DB foundation/UoW | High | None | 1 |
| Database persistence | Business DB as source of truth | One import payload row + files | No normalized business persistence | Critical | Foundation | 1 |
| Migration | Reproducible migrations from empty DB | Runtime `create_all` | No Alembic/migrations | High | Foundation | 1 |
| Seed | Idempotent base/store seed | None | No seed mechanism | Medium | Migrations | 1 |
| Error envelope | code/message/details/request_id | Partial handlers, no request_id | Contract mismatch/domain errors absent | High | Foundation | 1 |
| Store isolation | Filter every resource by store | No authorization/filtering | Cross-store exposure possible | Critical | Store model, auth | 1/3A |
| Idempotency | Key handling + unique constraints | None | Duplicate writes on retries | Critical | DB constraints | 1/2 |
| Audit log | Persist important writes | None | No audit trail | High | DB/session | 1 then per checkpoint |
| Import persistence | Jobs/files/profiles/mappings/issues tables | One JSON text payload | Not queryable/normalized | High | DB foundation | 2 |
| Import transaction | Atomic canonical persistence | File then row update | Partial writes possible | Critical | Business tables/UoW | 2 |
| Canonical business tables | Catalog/inventory/history/config | Canonical JSON only | All business tables missing | Critical | Migrations | 1/2 |
| Bootstrap | Reload complete store state | Missing | Frontend cannot reload DB state | High | 3A/3B tables | 3B |
| Dashboard | KPI/freshness/warnings | Missing | No API | Medium | Inventory/history | 3B |
| Inventory APIs | Read/count/adjust | Missing | No API/business workflow | High | Inventory schema | 3B |
| Inventory movements | Traceable movement ledger | Missing | No source of truth/audit | High | Inventory tables | 3B |
| Ingredient APIs | Store catalog CRUD | Missing | No API | High | Catalog tables | 3A |
| Product APIs | Store product CRUD | Missing | No API | High | Catalog tables | 3A |
| Recipe versioning | Effective versions + locking | Import rows only | No versions/locking | High | Product/ingredient | 3A |
| History APIs | Sales/usage/purchase pagination | Result JSON only | No DB queries/API | High | Import persistence | 3B |
| Supplier constraints | Cost/MOQ/pack/lead-time versions | Canonical JSON only | No authoritative constraints | High | Catalog suppliers | 3A |
| Aliases | Store alias CRUD | Missing | No API/table | Medium | Ingredients | 3A |
| Settings | Store policy/budget | Canonical generic rows only | No authoritative settings | High | Store table | 3A |
| Calendar features | Store calendar upsert/read | Canonical JSON only | No API/table | Medium | Store table | 3A |
| Forecast run shell | Persist blocked/not-implemented runs | Missing | No honest scaffold | Medium | Store/history foundation | 4 |
| Planning run shell | Persist blocked/not-implemented runs | Missing | No honest scaffold | Medium | Forecast shell/config | 5 |
| Purchase Order | Draft/state/version/cost workflows | Missing | No PO capability | High | Plan recommendations + inventory | 5 |
| Node proxy compatibility | Server-side key + explicit allowlist | Only docs/contract in repo | Cannot verify/update here | Medium | New endpoint list | 6/external repo |
| Tests | Contract/isolation/idempotency/rollback | 9 import-focused tests | Major critical-path gaps | High | Each feature | Every checkpoint |
| Documentation | API and checkpoint docs | README + two required docs | Operational/migration docs pending | Low | Implementation | Every checkpoint |
| Security | Production auth, isolation, safe errors | API key optional; no store ACL | Production boundary incomplete | Critical | Store/auth/error layers | 1 and 6 |

## 12. Known Bugs and Technical Risks

| Finding | Status | Evidence / impact |
|---|---|---|
| Correct mapping can retain stale “Missing core fields” warning | Likely; needs runtime reproduction | Qwen raw warnings are retained and validator warnings appended in `_validate_result()` |
| Rule warnings are directly copied after Qwen mapping | Not found | Code passes rule in prompt but does not programmatically append rule warnings |
| Validator uses old mapping after Qwen | Not found | `_validate_result()` passes `raw["column_mapping"]` |
| Warnings and mapping come from different objects | Likely only through model echo | Final mapping is Qwen raw; raw model warnings may describe rule suggestion |
| Low confidence forces review despite correct manual mapping | Not found for manual confirm | Confirm sets confidence 1.0 and uses warnings; rule mapping does use hardcoded 0.82 |
| Configured threshold conflicts with rule's hardcoded review threshold | Confirmed | pipeline uses setting; `map_sheet_rules()` hardcodes 0.82 |
| CORS origins require JSON but notebook sends CSV | Not found | validator explicitly supports comma-separated string; `.env.example` uses CSV |
| Quick Tunnel DNS unstable | Needs operational reproduction | README warns tunnel/runtime instability; no tunnel implementation in repo |
| Import state lost after restart | Not confirmed as stated | SQLite payload persists; loss occurs only if ephemeral runtime storage is lost |
| Canonical business data is not durable authoritative DB state | Confirmed | only import payload + JSON file, no business tables |
| Process has no workflow transaction | Confirmed | filesystem write precedes independent DB update |
| Process retries may duplicate future business rows | Confirmed architectural risk | no idempotency/checksum/row hash; currently no business table inserts |
| Router catches `KeyError` too broadly | Confirmed | confirm/process/result wrap all `KeyError` as import 404 |
| API response fields/statuses inconsistent | Confirmed | create `sheets`; status/get `mappings`; contract profiles/mappings and different statuses |
| Confirm can set status confirmed while review remains | Confirmed | status hardcoded `confirmed`, aggregate flag may remain true |
| Process allowed before confirmation | Confirmed | `process()` only requires import existence |
| Invalid canonical rows are persisted in result | Confirmed | validator reports errors but service extends all normalized rows |
| File/DB state can diverge | Confirmed | upload/result filesystem writes are outside DB transaction |
| File-too-large status mismatch | Confirmed | current 400 vs contract 413 |
| Unknown errors violate error envelope | Confirmed | no catch-all contract handler/request_id |
| Unit vocabulary differs from contract | Confirmed | normalizer emits English names and extra packaging units |
| Local SQLite concurrency/scaling limitation | Likely | sync service/repository and local files; requires load testing |
| Qwen load error startup diagnostics insufficient | Needs runtime reproduction | exception propagates, but no structured model-ready error/log wrapper |

## 13. Backward Compatibility Risks

1. Replacing `sheets` with `profiles`/`suggested_mappings` would break the current
   frontend/tests. Add fields from one source; deprecate later.
2. Replacing `sheet_id` immediately with `profile_id` breaks confirm payloads.
   Accept both during a documented transition and persist one canonical ID.
3. Renaming `awaiting_review`/`processed` to contract statuses can break polling.
   Add compatibility mapping or versioned status semantics and contract tests.
4. Moving canonical result from embedded JSON/files into tables can alter row
   order, metadata and units. Preserve result shape while changing its source.
5. Moving `ImportModel` or introducing a new metadata base can cause duplicate
   table definitions/import cycles. Establish one declarative base in Checkpoint 1.
6. Removing `create_all` before Alembic bootstrap is ready can break new local/test
   databases. Change only with migration/test database setup in place.
7. Enforcing nonempty API key in all environments would break existing tests/local
   startup. Enforce by environment, preserving explicit test/development behavior.
8. Correcting error codes/statuses is externally visible. Provide compatibility
   tests and coordinate proxy/frontend adapters.
9. Unit normalization migration can make old results disagree with new rows.
   Store canonical units and document legacy result representation.
10. Adding store filters without assigning legacy imports to stores could make old
    imports inaccessible. Backfill from existing `payload.store_id`.

## 14. Recommended Checkpoint Plan

### Checkpoint 1 — Database foundation

- **Goal:** establish `app/db`, shared declarative base/session/UoW, Alembic,
  foundational store/idempotency/audit/import and canonical table migrations,
  domain error envelope and test DB migration path.
- **Likely files added:** `app/db/base.py`, `session.py`, migration configuration,
  initial revisions, `app/models/*`, domain exceptions, migration/DB fixtures,
  optional idempotent seed command.
- **Likely files modified:** `app/main.py`, `config.py`, `dependencies.py`,
  repository wiring, tests/conftest, requirements.
- **Tables:** stores, settings foundation, import metadata foundation,
  idempotency records, audit logs and agreed canonical tables.
- **Endpoints:** no new business endpoint required; preserve all 8.
- **Tests:** empty-DB migration, session rollback, API error envelope, auth config,
  two-store repository isolation, legacy import table compatibility.
- **Compatibility risk:** highest around existing `imports` table and `create_all`.
- **Dependency:** none.
- **Intentionally not:** import rewrite, catalog APIs, forecast/model/optimizer.

### Checkpoint 2 — Import persistence

- **Goal:** normalize import job/file/profile/mapping/issue persistence and
  atomically write canonical business rows, with checksum/row hash/idempotency.
- **Files added:** import ORM models/repositories, checksum/hash utilities, domain
  import exceptions.
- **Files modified:** `ImportService`, SQLite repository adapter or replacement,
  import schemas/router, ingestion persistence boundary.
- **Tables:** import_jobs/files/sheet_profiles/mappings/issues and canonical target
  tables/source metadata.
- **Endpoints:** existing five import endpoints only.
- **Tests:** restart persistence, repeated upload/process, rollback injection,
  partial/invalid mapping state, compatibility fields and two-store isolation.
- **Compatibility risk:** `sheets` vs profiles, sheet/profile IDs, statuses,
  canonical result shape.
- **Dependency:** Checkpoint 1.
- **Intentionally not:** new catalog/inventory/forecast/PO APIs; no Qwen changes
  beyond mocks needed for persistence tests.

### Checkpoint 3A — Catalog and configuration

- **Goal:** store-scoped ingredients, products, versioned recipes, suppliers/
  constraints, aliases, settings and calendar.
- **Files added:** routers/schemas/services/repositories/models per domain.
- **Files modified:** router registration, DI, shared pagination/error utilities.
- **Tables:** ingredients, aliases, products, recipe versions/lines, suppliers,
  supplier terms, store settings, calendar features.
- **Endpoints:** contract catalog/recipe/supplier/alias/settings/calendar routes.
- **Tests:** CRUD, validation, duplicate recipe ingredient, version conflict,
  store isolation, supplier MOQ/pack validation.
- **Compatibility risk:** imported names must resolve to the same catalog IDs;
  legacy canonical unit aliases.
- **Dependency:** Checkpoints 1–2.
- **Intentionally not:** inventory movement workflows, model output, PO.

### Checkpoint 3B — Inventory, history and bootstrap

- **Goal:** movement-backed inventory, historical reads, bootstrap/dashboard from
  database.
- **Files added:** inventory/history/bootstrap modules.
- **Files modified:** import canonical persistence, router registration.
- **Tables:** inventory lots/movements/counts/adjustments, sales_daily,
  usage_daily, purchase receipts; possibly materialized balances.
- **Endpoints:** inventory read/count/adjust, movement history, three history APIs,
  bootstrap and dashboard.
- **Tests:** count/adjust success, negative-stock rejection, idempotency, rollback,
  pagination/date filters, reload, two-store access, deterministic freshness/KPI.
- **Compatibility risk:** current canonical result must still be reproducible;
  inventory balance source must not double-count imports.
- **Dependency:** 3A catalog IDs and Checkpoint 2 provenance.
- **Intentionally not:** demand-based days supply, projected stockout or expiry
  risk.

### Checkpoint 4 — Forecast API scaffolding

- **Goal:** persistent run/status/result shell that honestly reports blocked
  `engine_status: not_implemented`.
- **Files added:** forecast schemas/models/repository/service/router.
- **Files modified:** router registration, bootstrap latest-runs.
- **Tables:** forecast_runs; forecast_points schema may be migrated but remains
  empty until real engine checkpoint.
- **Endpoints:** create/status/result forecast routes.
- **Tests:** idempotency, store isolation, persistence/reload, blocked response,
  null versions and no fake quantiles.
- **Compatibility risk:** clients may interpret accepted run as completed output.
- **Dependency:** store/history foundation.
- **Intentionally not:** LightGBM, calibration, P25/P50/P75, drivers/confidence.

### Checkpoint 5 — Plan and PO scaffolding

- **Goal:** persistent blocked plan shell; implement PO only when backed by real
  recommendations. If optimizer remains absent, do not create Draft PO.
- **Files added:** plan/PO domain modules and state-machine policies.
- **Files modified:** router registration, bootstrap/dashboard if applicable.
- **Tables:** plan_runs, recommendations, PO headers/lines, receipts; recommendation
  rows remain empty until optimizer.
- **Endpoints:** plan create/status/result; PO endpoints may return domain
  `MODEL_NOT_READY` until recommendations exist.
- **Tests:** blocked plan, no fake recommendations, no PO from empty plan, store
  isolation, idempotency, version conflict, invalid transition, confirm rollback.
- **Compatibility risk:** frontend must handle blocked engines/null budget values.
- **Dependency:** 3A/3B and Checkpoint 4.
- **Intentionally not:** optimizer, fake quantities/probabilities, authoritative PO
  creation without recommendations.

### Checkpoint 6 — Hardening and integration

- **Goal:** production security/config validation, request IDs/logging, audit
  completeness, CORS/proxy coordination, performance and recovery testing.
- **Files added:** request-id/logging middleware, operational docs, integration and
  security tests.
- **Files modified:** config/startup, handlers, all write services for audit,
  deployment/proxy repo documentation as available.
- **Tables:** indexes/constraints/audit improvements only through migrations.
- **Endpoints:** no speculative business expansion; contract conformance fixes.
- **Tests:** auth, error envelope, load/concurrency, file failure recovery, proxy
  allowlist contract, migration upgrade/rollback rehearsal.
- **Compatibility risk:** stricter auth/status/error behavior.
- **Dependency:** all prior checkpoints.
- **Intentionally not:** model/optimizer unless a dedicated checkpoint authorizes
  them.

Thứ tự 3A trước 3B là hợp lý vì inventory/history cần stable ingredient/product/
supplier IDs. Forecast shell đứng sau deterministic DB reads để run có correct
store/cutoff provenance, dù chưa có model.

## 15. Files Likely to Change by Checkpoint

| Existing file/module | 1 | 2 | 3A | 3B | 4 | 5 | 6 |
|---|---:|---:|---:|---:|---:|---:|---:|
| `app/main.py` | ✓ |  | ✓ | ✓ | ✓ | ✓ | ✓ |
| `app/config.py` | ✓ | ✓ |  |  | ✓ |  | ✓ |
| `app/dependencies.py` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `app/api/imports.py` |  | ✓ |  |  |  |  | ✓ |
| `app/schemas/imports.py` |  | ✓ |  |  |  |  | ✓ |
| `app/services/import_service.py` |  | ✓ | ✓ | ✓ |  |  | ✓ |
| `app/repositories/imports.py` | ✓ | ✓ |  |  |  |  |  |
| `app/repositories/sqlite_imports.py` | ✓ | ✓ |  |  |  |  |  |
| `app/core/*` |  | minimal | minimal | minimal |  |  | tests/docs |
| `app/llm/*` |  | mock tests only |  |  |  |  | health/log tests |
| `tests/conftest.py` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `requirements.txt` / `pyproject.toml` | ✓ |  |  |  |  |  | optional |
| New `app/db`, `app/models`, migrations | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | indexes |
| New domain API/schema/service/repository |  | import | catalog | inventory | forecast | plan/PO | middleware |

`app/core` và `app/llm` phải được giữ ổn định; chỉ sửa khi checkpoint có bug/test
cụ thể và chứng minh regression safety.

## 16. Open Questions

1. Production database đích vẫn là SQLite hay sẽ là PostgreSQL? Thiết kế migration
   nên tránh SQLite-only assumptions dù giữ local SQLite support.
2. `store_id` là external string như `STORE_001` hay UUID nội bộ + external code?
3. API key cấp toàn hệ thống hay map tới tập stores? Header hiện chỉ xác thực key,
   chưa authorize store.
4. Có phải backfill runtime `shelfcash.db`/uploads hiện hữu hay chỉ cần migration
   cho dữ liệu production được cung cấp riêng?
5. Compatibility window cho `sheets`/`sheet_id` và old statuses kéo dài bao lâu?
6. Contract canonical mapping dùng field ví dụ `ingredient`, trong khi ingestion
   dùng `ingredient_name`; adapter chuẩn nằm ở import persistence hay contract sẽ
   chốt lại?
7. CSV được contract nói frontend có thể gửi, nhưng reader hiện không hỗ trợ CSV.
   Checkpoint nào sẽ mở rộng, nếu có?
8. Invalid canonical rows nên block toàn import, persist issues và chỉ ghi valid
   rows, hay cho policy theo sheet type?
9. File storage production dùng local disk hay object storage? Transaction/recovery
   strategy phụ thuộc quyết định này.
10. Budget confirm policy là reserve hay deduct, và receive/cancel semantics là gì?
11. Node proxy nằm repository nào để contract test allowlist có thể chạy?
12. Request ID do proxy cấp hay backend tạo khi thiếu?

## 17. Final Recommendation

Tiếp tục với **Checkpoint 1 — Database foundation**, nhưng giữ phạm vi hẹp: shared
DB/session architecture, Alembic migration từ database trống, foundational
store/isolation/idempotency/audit structures, error envelope và compatibility test
cho tám route hiện tại. Không chuyển import canonical writes sang business tables
trong cùng checkpoint nếu chưa có transaction/idempotency design được test; việc đó
thuộc Checkpoint 2.

Không thay FastAPI, không thay provider, không rewrite ingestion pipeline, không
đổi response import hiện tại. Mọi schema/route mới về sau phải store-scoped từ câu
query đầu tiên, có transaction/idempotency và được chứng minh bằng tests hai store.

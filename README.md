# ShelfCash Backend

FastAPI backend nhận Excel từ React, profile workbook, đề xuất mapping bằng rules/Qwen, cho người dùng xác nhận, rồi chuẩn hóa thành canonical JSON ShelfCash.

## Kiến trúc

Luồng xử lý là `API → ImportService → IngestionPipeline → Excel profiler / Rule mapper / LLMProvider → Normalizer / Validator → SQLite + JSON result`. Repository lưu trạng thái import trong SQLite; file upload và kết quả nằm trong `runtime/`. Mapping luôn được validate theo canonical schema. Pipeline chỉ gửi `SheetProfile` và tối đa 8 sample rows vào Qwen, không gửi toàn bộ workbook.

`LLMProvider` tách inference khỏi pipeline. `DisabledLLMProvider` chạy CPU/CI, còn `LocalQwenProvider` lazy-import torch/transformers, load model đúng một lần trong FastAPI lifespan, chạy 4-bit NF4 và serialize inference bằng semaphore.

## Chạy local không LLM

Yêu cầu Python 3.11:

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
copy .env.example .env
set LLM_PROVIDER=disabled
alembic upgrade head
python -m scripts.seed_database
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

PowerShell dùng `$env:LLM_PROVIDER="disabled"`. Linux/macOS có thể dùng:

```bash
LLM_PROVIDER=disabled uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Database và migration

Database URL được đọc từ `DATABASE_URL`; mặc định local là:

```dotenv
DATABASE_URL=sqlite:///runtime/shelfcash.db
```

Sau khi cài dependencies và trước khi chạy app, tạo hoặc nâng cấp schema:

```bash
alembic upgrade head
python -m scripts.seed_database
```

Seed tạo `STORE_001` và `STORE_TEST_001`. Có thể chạy seed nhiều lần: store đã có
được giữ nguyên và không bị overwrite.

Lệnh migration cũng áp dụng trực tiếp cho database legacy đã có bảng `imports`.
Revision foundation kiểm tra bảng này trước khi tạo, giữ nguyên tên bảng, các cột,
primary key, payload và rows hiện hữu, rồi bổ sung `stores`,
`idempotency_records`, `audit_logs` và `alembic_version`. Không cần xóa database
hoặc chạy SQL thủ công.

Application startup không gọi `create_all` và không tự chạy migration. Alembic là
nguồn quản lý schema cho development và production; cần chạy `alembic upgrade
head` rõ ràng khi deploy. Test suite tự tạo SQLite database cô lập và chạy
migration vào database đó.

Checkpoint database foundation này chưa triển khai catalog, inventory, history,
bootstrap/dashboard, forecast, planning hoặc Purchase Order APIs. Import payload
legacy và các response `sheets`, `mappings`, `sheet_id`, status hiện tại vẫn được
giữ nguyên.

Import workflow hiện dùng các bảng normalized `import_jobs`, `import_files`,
`import_sheet_profiles`, `import_mappings` và `import_issues`; bảng `imports` vẫn
được duy trì làm compatibility cache. Upload hỗ trợ `.xlsx`, `.xls`, `.xlsm`,
`.csv`, mặc định tối đa 10 file, 12 MB/file và 50 MB/request. `POST /imports` hỗ
trợ `Idempotency-Key`. Chi tiết transition, status và lazy backfill:
[`docs/IMPORT_PERSISTENCE.md`](docs/IMPORT_PERSISTENCE.md).

Revision `20260728_0003` adds the canonical, store-scoped business schema and
repository foundation. Apply it with `alembic upgrade head`; details are in
[`docs/CANONICAL_BUSINESS_SCHEMA.md`](docs/CANONICAL_BUSINESS_SCHEMA.md).
Checkpoint 2B1 does not make the import processor write business rows.

Revision `20260728_0004` connects confirmed import processing to those canonical
business tables with all-or-nothing transactions, deterministic provenance and
natural-key correction/deduplication. Run `alembic upgrade head` before starting
the app. See
[`docs/IMPORT_TO_BUSINESS_PERSISTENCE.md`](docs/IMPORT_TO_BUSINESS_PERSISTENCE.md).
Public business CRUD APIs and forecast/plan/PO remain unimplemented.

Revision `20260728_0005` adds version fields for catalog concurrency and exposes
contract-aligned store-path ingredient, product, active-recipe and alias-read APIs. Apply it with
`alembic upgrade head`; API details are in
[`docs/CATALOG_AND_RECIPE_API.md`](docs/CATALOG_AND_RECIPE_API.md). Seed stores
remain `STORE_001` and `STORE_TEST_001`. Supplier/settings/calendar APIs belong
to Checkpoint 3A2; inventory/history/bootstrap/forecast/plan/PO are still absent.
Alias-list PUT accepts an `aliases` array and performs transactional additive
bulk upsert scoped by the store path.

Test và fake data:

```bash
pytest -q
python scripts/create_fake_excel.py
```

## Kaggle GPU với Qwen

Không cài lại hoặc pin PyTorch; dùng bản PyTorch/CUDA sẵn có của Kaggle:

```bash
pip install -r requirements-kaggle.txt
export LLM_PROVIDER=local_qwen
export QWEN_MODEL_ID=Qwen/Qwen3-4B
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Model được load lúc startup, mặc định 4-bit NF4 với `device_map="auto"`. Kaggle notebook và Quick Tunnel chỉ phù hợp demo, không phải production: runtime có thể ngắt, URL thay đổi, và tunnel không thay thế authentication/network controls.

## API và curl

Health:

```bash
curl http://localhost:8000/health
curl http://localhost:8000/api/v1/llm/health
```

Upload:

```bash
curl -X POST http://localhost:8000/api/v1/imports \
  -H "X-ShelfCash-Key: $SHELFCASH_API_KEY" \
  -F "files=@runtime/fake_shelfcash.xlsx" \
  -F "store_id=STORE_001" \
  -F "forecast_date=2026-07-27" \
  -F "forecast_horizon=7"
```

Sau upload, gọi `GET /api/v1/imports/{id}`, `POST /confirm`, `POST /process`, rồi `GET /result`. Body confirm:

```json
{
  "mappings": [{
    "sheet_id": "opaque-sheet-id",
    "sheet_type": "business_constraints",
    "column_mapping": {
      "Loại điều kiện": "constraint_type",
      "Giá trị": "value"
    }
  }]
}
```

Smoke-test Qwen độc lập qua `POST /api/v1/llm/map-sheet` với body `{"profile": {...}}`.

## React

```typescript
const form = new FormData();

for (const file of files) {
  form.append("files", file);
}

form.append("store_id", "STORE_001");
form.append("forecast_date", "2026-07-27");
form.append("forecast_horizon", "7");

await fetch(`${API_URL}/api/v1/imports`, {
  method: "POST",
  headers: {
    "X-ShelfCash-Key": API_KEY,
  },
  body: form,
});
```

Không tự đặt `Content-Type` khi dùng `FormData`; browser phải tự thêm multipart boundary. Nếu `SHELFCASH_API_KEY` rỗng thì API key bị tắt. Khi có giá trị, mọi endpoint `/api/v1` trừ `/api/v1/llm/health` yêu cầu `X-ShelfCash-Key`; `/health` luôn public.

## Thêm remote provider

Tạo `RemoteLLMProvider` implement `available`, `health`, `map_sheet` từ `app.llm.base.LLMProvider`, thêm lựa chọn trong `app/llm/factory.py`, và bổ sung settings. Ingestion pipeline không cần sửa. Provider phải trả `MappingSuggestion`; output bên ngoài vẫn phải validate sheet type, confidence, source columns và canonical fields trước khi sử dụng.

## Giới hạn và an toàn

Hỗ trợ `.xlsx`, `.xls`, `.xlsm`; macro không được thực thi và công thức không được đánh giá. Tên file được sanitize. Mặc định tối đa 10 file/request, 20 MB/file, 30 sheet/file, 100.000 dòng/sheet. MVP xử lý đồng bộ, lưu local SQLite/filesystem, không có distributed jobs. Unit được chuẩn hóa theo tên nhưng không suy đoán conversion factor (ví dụ thùng sang hộp). Không log prompt đầy đủ hoặc dữ liệu workbook.

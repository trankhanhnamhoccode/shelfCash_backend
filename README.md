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
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

PowerShell dùng `$env:LLM_PROVIDER="disabled"`. Linux/macOS có thể dùng:

```bash
LLM_PROVIDER=disabled uvicorn app.main:app --host 0.0.0.0 --port 8000
```

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

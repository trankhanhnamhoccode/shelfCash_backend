# ShelfCash Normalized Import Persistence

Checkpoint 2A chuyển workflow import sang normalized database persistence nhưng
giữ nguyên public import API hiện tại.

## Tables

- `import_jobs`: store, workflow state, timestamps, result và validation summary.
- `import_files`: file metadata, SHA-256 checksum, size và internal storage path.
- `import_sheet_profiles`: profile, sample rows giới hạn và parsed rows phục vụ
  normalization.
- `import_mappings`: một active/final mapping cho mỗi profile.
- `import_issues`: warning/error theo profile, row và processing stage.

Raw file binary không được lưu trong database. `stored_path`, checksum và full
parsed rows không xuất hiện trong public response.

## Legacy compatibility

Bảng `imports` không bị drop hoặc đổi schema. Normalized tables là source of truth
mới; row `imports` được cập nhật như compatibility cache từ cùng normalized
aggregate.

Public API tiếp tục giữ:

```text
sheets
mappings
sheet_id
awaiting_review
confirmed
processed
```

Các field additive `profiles`, `suggested_mappings` và `profile_id` được tạo từ
cùng profile/mapping rows nên không có hai bản mapping độc lập.

Import legacy chưa có `import_jobs` được lazy-backfill khi đọc lần đầu. Backfill
giữ `import_id`, `store_id`, `sheet_id`, mappings, status và result, đồng thời giữ
nguyên row legacy. Nếu legacy store chưa tồn tại, một store placeholder mang đúng
external `store_id` được tạo để thỏa foreign key; cần review/đổi tên placeholder
bằng quy trình quản trị ở checkpoint sau.

## Status mapping

| Internal status | Public compatibility status |
|---|---|
| `uploaded` | `awaiting_review` |
| `mapping_required` | `awaiting_review` |
| `confirmed` | `confirmed` |
| `processing` | `confirmed` |
| `completed` | `processed` |
| `failed` | `failed` |

Create tạo `mapping_required`. Process chỉ chạy từ `confirmed`; gọi lại khi
`completed` trả result đã lưu mà không normalize lần nữa.

## Create, idempotency và file safety

`POST /api/v1/imports` kiểm tra store tồn tại và hỗ trợ `Idempotency-Key`.
Request hash gồm store, forecast context, filenames và file checksums:

- cùng key/cùng hash trả lại cùng `import_id`, giữ HTTP 201;
- cùng key/hash khác trả 409 `DUPLICATE_REQUEST`;
- key được scope theo store.

SHA-256 được tính trực tiếp từ bytes upload. File được ghi vào temporary path;
metadata, job, profiles, mappings, issues, idempotency và audit dùng chung một DB
transaction. Temporary file được atomic rename trước commit. Parse, filesystem
hoặc commit failure sẽ rollback DB và xóa file tạm/final đã tạo.

Giới hạn mặc định:

```text
10 files/request
12 MB/file
50 MB/request
30 sheets/workbook
100,000 rows/sheet
8 sample rows/profile
forecast_horizon 1..90
```

Hỗ trợ `.xlsx`, `.xls`, `.xlsm` và `.csv`. Parser thực sự đọc nội dung; không tin
chỉ extension hoặc content type.

## Mapping confirmation

Confirm chấp nhận `sheet_id` legacy hoặc normalized `profile_id`. Nếu gửi cả hai,
chúng phải trỏ cùng profile. `skip: true` lưu final mapping `unknown` và process bỏ
qua sheet nhưng vẫn giữ trace.

Structural warnings luôn được deterministic validator tính lại từ final mapping.
Warning cũ như `Missing core fields` không được giữ nếu Qwen mapping cuối đã đầy
đủ. Semantic warning không thuộc nhóm structural vẫn được giữ. Rule mapper,
pipeline và Qwen post-validation dùng chung `rule_confidence_threshold`.

## Process và result

Process vẫn normalize/validate canonical JSON như trước. Result và validation
summary trong `import_jobs` là source of truth; JSON file trong `runtime/results`
chỉ là compatibility/export artifact. Invalid rows chưa bị bỏ để giữ behavior cũ;
row issues và validation summary cung cấp trace.

Checkpoint 2A không ghi canonical rows vào ingredients, products, inventory,
sales, usage, recipes, supplier hoặc calendar tables. Việc đó thuộc Checkpoint 2B.
Không có forecast, planning, optimizer, recommendation hoặc Purchase Order.

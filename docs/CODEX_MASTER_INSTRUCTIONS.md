# ShelfCash Backend — Codex Master Instructions

Tài liệu này là bộ nguyên tắc bắt buộc cho mọi checkpoint phát triển backend
ShelfCash. Mỗi checkpoint phải đọc tài liệu này và
`docs/ShelfCash_API_Contract_v1.md` trước khi sửa code.

## 1. Source of truth

- `docs/ShelfCash_API_Contract_v1.md` là source of truth về API đích.
- Code và tests hiện tại là source of truth về chức năng đang chạy thực tế.
- Khi contract khác code hiện tại, checkpoint phải: phân tích và ghi rõ khác biệt;
  ưu tiên không phá frontend, import và Qwen đang chạy; giữ backward compatibility
  khi hợp lý; chỉ rõ compatibility field hoặc migration path; tuyệt đối không âm
  thầm đổi request/response.
- Contract không tự động cho phép triển khai ngoài phạm vi checkpoint.

## 2. Không xây lại repository từ đầu

- Tái sử dụng module hiện có khi có thể; không thay toàn bộ import pipeline chỉ để
  viết lại đẹp hơn.
- Không đổi framework, không thay FastAPI, Qwen provider hoặc database technology
  nếu checkpoint không yêu cầu và codebase đã có giải pháp phù hợp.
- Refactor lớn phải có lý do cụ thể và tests chứng minh không regression.
- Bảo toàn các ranh giới đang hữu ích: provider interface, repository interface,
  service, ingestion pipeline và schema validation.

## 3. Giữ các API hiện tại hoạt động

Không được phá:

```text
GET  /health
GET  /api/v1/llm/health
POST /api/v1/llm/map-sheet
POST /api/v1/imports
GET  /api/v1/imports/{import_id}
POST /api/v1/imports/{import_id}/confirm
POST /api/v1/imports/{import_id}/process
GET  /api/v1/imports/{import_id}/result
```

Nếu mở rộng response, ưu tiên thêm field thay vì đổi nghĩa, đổi tên hoặc xóa field
cũ. Bảo toàn status code và luồng upload → review → confirm → process → result,
trừ khi contract yêu cầu thay đổi có migration path rõ ràng.

## 4. Kiến trúc phân lớp

Kiến trúc tối thiểu:

```text
app/
  api/
  schemas/
  models/
  repositories/
  services/
  core/
  db/
  llm/
  utils/
```

- Router chỉ nhận request, gọi service và ánh xạ domain exception sang HTTP
  response; không chứa transaction hoặc business logic lớn.
- Service điều phối workflow và business validation.
- Repository chịu trách nhiệm truy vấn/persistence database.
- Schema chịu trách nhiệm validation request/response.
- Không dùng ORM model trực tiếp làm public API response.
- LLM provider không truy cập router hoặc database trực tiếp.
- Import pipeline phải tách rõ:
  `profile → mapping → confirmation → normalization → persistence`.
- Dependency injection phải được dùng để thay provider/repository trong tests;
  tránh global mutable state.

## 5. Database là source of truth

- Không dùng React state, dictionary in-memory hoặc JSON file làm persistence
  production chính. JSON result hiện tại chỉ là compatibility artifact trong khi
  persistence nghiệp vụ được chuyển vào database.
- Tồn kho phải được đọc/tính từ database. Recipe phải có version. Forecast run,
  plan run và PO phải còn tồn tại sau reload.
- Mọi resource nghiệp vụ phải gắn đúng `store_id`; không cho cross-store access.
- SQLite/import repository hiện hữu phải được tiến hóa có kiểm soát, không bị thay
  bỏ tùy tiện.

## 6. Transaction

Các thao tác nhiều bảng phải chạy trong một transaction, đặc biệt:

- import process;
- inventory count và inventory adjustment;
- recipe version update;
- supplier constraint update;
- purchase order create, confirm và receive.

Nếu lỗi giữa chừng: rollback toàn bộ, không để dữ liệu nửa vời, ghi log phù hợp và
trả error JSON đúng contract. Transaction thuộc service/repository boundary, không
thuộc router.

## 7. Idempotency và chống trùng

Hỗ trợ `Idempotency-Key` ít nhất cho:

```text
POST /api/v1/imports
POST /api/v1/stores/{store_id}/inventory-counts
POST /api/v1/stores/{store_id}/inventory-adjustments
POST /api/v1/stores/{store_id}/forecast-runs
POST /api/v1/stores/{store_id}/plan-runs
POST /api/v1/stores/{store_id}/purchase-orders
```

Import chống trùng bằng `import_id`, file checksum, `source_row_hash` và database
unique constraints khi phù hợp. Gọi process nhiều lần với cùng `import_id` phải
trả lại kết quả an toàn hoặc trạng thái hợp lệ, không tạo record nghiệp vụ trùng.
Idempotency phải có scope theo store/operation và không tái sử dụng response cho
payload khác.

## 8. Store isolation

- Mọi resource nghiệp vụ thuộc một `store_id`.
- Query resource theo ID phải đồng thời filter `store_id`; không query chỉ bằng
  UUID, trả dữ liệu rồi mới kiểm tra store.
- Tests phải có ít nhất hai store. Resource của `STORE_A` không được đọc hoặc sửa
  qua endpoint của `STORE_B`.
- Chống cross-store cả ở resource cha/con, import, recipe, run, recommendation và
  PO.

## 9. Authentication hiện tại

- Giữ header `X-ShelfCash-Key`; đọc API key từ `SHELFCASH_API_KEY`.
- Không hardcode hoặc log API key; không đưa API key vào React, response hay test
  snapshot.
- Node.js proxy tự gắn key ở server side. Browser chỉ gọi proxy cùng domain.
- Không trả secret environment variable dưới bất kỳ dạng nào.

## 10. Error contract

Mọi lỗi API phải có dạng:

```json
{
  "code": "VALIDATION_ERROR",
  "message": "Dữ liệu không hợp lệ.",
  "details": {},
  "request_id": null
}
```

Không trả exception string thô, stack trace hoặc dữ liệu nhạy cảm. Không bắt chung
`KeyError` rồi biến mọi lỗi thành 404. Dùng domain exception riêng, tối thiểu theo
nhu cầu:

```text
ShelfCashError
ValidationError
StoreNotFoundError
ResourceNotFoundError
VersionConflictError
DuplicateRequestError
ImportNotFoundError
ImportNotReadyError
MappingIncompleteError
ModelNotReadyError
BudgetExceededError
InvalidStateTransitionError
```

Exception handler tập trung phải ánh xạ ổn định sang HTTP status/code trong
contract. `request_id` có thể `null` cho tới khi middleware request ID tồn tại,
nhưng field không được bị thiếu.

## 11. Dữ liệu và đơn vị

- JSON dùng `snake_case`; date dùng `YYYY-MM-DD`; datetime dùng ISO 8601 có
  timezone; timezone mặc định `Asia/Ho_Chi_Minh`.
- VND dùng integer. Quantity dùng JSON number và không âm, ngoại trừ movement delta
  nội bộ có chủ đích.
- Đơn vị chuẩn MVP: `kg`, `g`, `lít`, `ml`, `cái`.
- Alias `l`, `liter`, `litre` phải normalize thành `lít`.
- Không tự convert giữa mass, volume và count nếu chưa có conversion rule.
- Khi chỉnh normalizer hiện tại, phải có compatibility/migration cho dữ liệu đơn vị
  tiếng Anh đã sinh trước đây; không tạo hai biểu diễn mâu thuẫn.

## 12. Import và Qwen

- Rule mapper luôn chạy trước. Chỉ gọi Qwen khi rule confidence dưới threshold
  hoặc mapping chưa đầy đủ.
- Sau Qwen phải validate mapping cuối; validator kiểm tra target field từ
  `column_mapping.values()`, đồng thời kiểm tra đúng tập source columns.
- Không giữ warning/error lỗi thời từ rule mapping nếu mapping cuối đã thay đổi;
  warnings cuối phải được tính lại từ mapping cuối, không nối mù quáng.
- Unit tests phải mock/disable provider, không tải Qwen, không cần GPU.
- Không load model khi import module. Việc load có kiểm soát trong application
  lifespan/provider lifecycle; import `torch/transformers` phải tiếp tục lazy.
- Qwen chỉ hỗ trợ semantic mapping, không tính forecast hoặc order quantity.
- Không gửi toàn workbook cho Qwen; chỉ gửi `SheetProfile` và số sample rows giới
  hạn theo cấu hình (hiện tối đa 8).

## 13. Forecast và planning chưa được triển khai

Cho tới checkpoint model/optimizer tương ứng:

- Không tạo forecast, recommendation, order quantity, stockout probability,
  drivers hoặc confidence giả.
- Không hardcode P25/P50/P75, không dùng random, không gọi moving average là
  LightGBM.
- API scaffold phải nói rõ `status: "blocked"` và
  `engine_status: "not_implemented"`.

Forecast result mẫu:

```json
{
  "forecast_run_id": "...",
  "status": "blocked",
  "engine_status": "not_implemented",
  "model_version": null,
  "calibrator_version": null,
  "forecasts": [],
  "warnings": [{
    "code": "MODEL_NOT_READY",
    "message": "Forecast engine chưa được triển khai."
  }]
}
```

Không dùng quantile bằng `0`; số 0 là dự báo có nghĩa. Nếu schema bắt buộc, dùng
`null`.

Plan result mẫu:

```json
{
  "plan_run_id": "...",
  "status": "blocked",
  "engine_status": "not_implemented",
  "recommendations": [],
  "budget": {
    "limit": 2300000,
    "planned_cost": null,
    "remaining_after_plan": null
  },
  "warnings": [{
    "code": "MODEL_NOT_READY",
    "message": "Planning engine chưa được triển khai."
  }]
}
```

Không tạo Draft PO từ plan không có recommendations.

## 14. Deterministic logic được phép làm trước model

Có thể tính trực tiếp từ database: inventory `on_hand`, inventory value, expired
status, expiring status theo cấu hình, file checksum, row hash, unit normalization,
recipe version theo ngày, supplier MOQ, pack size validation, lead-time date, PO
line total, PO total, remaining budget theo policy, pagination, data freshness và
record counts.

Không gọi các giá trị sau là model output hoặc tự suy diễn khi engine chưa tồn tại:
`days_supply`, projected stockout date, forecast demand, stockout probability,
expiry risk dựa trên demand tương lai, recommended quantity. Chúng phải `null` hoặc
không xuất hiện.

## 15. Recipe versioning

- Không overwrite recipe cũ; thay đổi tạo `recipe_version` mới và đóng
  `effective_to` của version cũ.
- Historical demand reconstruction dùng đúng version có hiệu lực tại thời điểm
  lịch sử.
- Dùng optimistic locking bằng `version`; conflict trả HTTP 409
  `VERSION_CONFLICT`.
- Không duplicate ingredient trong cùng recipe version.
- Product và mọi ingredient phải thuộc cùng store.

## 16. Inventory

- Frontend không PATCH trực tiếp `on_hand`.
- Physical count tạo inventory count và movement; waste/expiry/manual adjustment
  tạo movement.
- Không cho tồn kho âm.
- Current inventory được tính từ movements hoặc materialized balance do backend
  duy trì nhất quán.
- Mọi inventory write có audit/source metadata và nằm trong transaction.

## 17. Purchase Order

- `total` và `budget_after` từ frontend không có thẩm quyền.
- Backend lấy unit cost từ supplier constraint, kiểm tra MOQ/pack size, tính
  `line_total`, total, delivery date và budget.
- Chỉ cho phép state transition hợp lệ. Draft sửa được với version control;
  ordered PO không sửa line trực tiếp.
- Không confirm một PO hai lần. Confirm phải audit và cập nhật budget/inbound đúng
  policy, trong một transaction.

## 18. Testing

Mỗi checkpoint phải:

1. Chạy tests hiện có trước khi sửa.
2. Thêm tests cho logic mới.
3. Không xóa test để pipeline pass.
4. Không skip test quan trọng mà không giải thích.
5. Không download Qwen và không yêu cầu GPU.
6. Có test phù hợp cho success, validation error, not found, cross-store access,
   idempotency, transaction rollback, version conflict và invalid state transition.

Chỉ kết thúc checkpoint khi `pytest -q` pass. Chỉ chạy `ruff check .` và
`mypy app` nếu tool đã được cấu hình hoặc checkpoint yêu cầu; không tự đưa tool vào
chỉ để thỏa checklist. Báo lệnh và kết quả thực tế, không chỉ ghi “PASS”.

## 19. Migration

- Migration phải chạy được từ database trống; không chỉnh database thủ công.
- Seed idempotent. Không dùng `create_all` thay migration trong production startup.
  `create_all` hiện tại là giới hạn legacy của import repository, không phải mẫu
  cho schema production mới.
- Tests có thể dùng isolated test database.
- Không xóa dữ liệu cũ không cần thiết; migration destructive phải được cảnh báo,
  có kế hoạch backup/rollback và nằm đúng phạm vi checkpoint.

## 20. Logging và bảo mật

Không log API key, raw Excel binary, toàn workbook, toàn sample rows, prompt đầy đủ
chứa dữ liệu khách hàng, secret environment variables hoặc stack trace trong API
response.

Có thể log có cấu trúc: `request_id`, endpoint, status, elapsed time, `store_id`,
`import_id`, `run_id`, file name, row count, error code, model provider và model
load status. File name cũng phải được sanitize trước khi log.

## 21. Backward compatibility

API hiện tại dùng `sheets`, trong khi contract mới có thể dùng `profiles` và
`suggested_mappings`; không được âm thầm xóa `sheets`.

Có thể giữ `sheets` làm compatibility field, thêm `profiles` và
`suggested_mappings`, sinh tất cả từ cùng một source, document deprecation và thêm
contract test. Không tạo field trùng nghĩa nhưng chứa dữ liệu mâu thuẫn. Áp dụng
nguyên tắc tương tự cho `mappings`, status code và error code legacy cho tới khi có
migration path được công bố.

## 22. Quy tắc checkpoint

- Mỗi prompt phải ghi rõ phạm vi; Codex chỉ làm checkpoint được giao.
- Không tự triển khai checkpoint tiếp theo; không tự thêm model, agent, optimizer
  hoặc thay đổi frontend nếu không được yêu cầu.
- Không commit nếu người dùng không yêu cầu.
- Không mở rộng schema/database/migration ngoài phạm vi.
- Báo rõ các phần cố ý chưa làm và mọi giả định compatibility.

## 23. Báo cáo hoàn thành checkpoint

Mỗi lần hoàn thành phải báo cáo:

1. Checkpoint.
2. Phạm vi đã thực hiện.
3. File đã thêm.
4. File đã sửa.
5. Migration đã tạo.
6. Endpoint đã thêm hoặc thay đổi.
7. Tests đã thêm.
8. Lệnh tests đã chạy.
9. Kết quả tests.
10. Backward compatibility.
11. Những gì chưa implement.
12. Rủi ro hoặc vấn đề còn lại.
13. Git status.

Không chỉ nói “PASS” mà không đưa bằng chứng. Nếu checkpoint có format báo cáo hẹp
hơn do người dùng yêu cầu, tuân thủ format của checkpoint nhưng vẫn phải cung cấp
bằng chứng được yêu cầu.

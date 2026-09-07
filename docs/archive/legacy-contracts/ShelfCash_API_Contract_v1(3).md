# ShelfCash API Contract v1 — Bản hợp nhất

**Phiên bản:** 1.1-consolidated  
**Ngày:** 2026-07-28  
**Trạng thái:** Implementation-ready cho MVP  
**Phạm vi:** React frontend + Node.js proxy + ShelfCash backend + database  
**Base path backend:** `/api/v1`

**Thay đổi so với 1.0-draft:** khóa contract cho alias bulk upsert, inventory
adjustment, sales batch, purchase-history backfill, calendar upsert, sửa Draft
PO, nhận hàng từ PO và policy ngân sách `reserve khi confirm → spent khi
receive`.

---

## 1. Mục tiêu của contract

Tài liệu này là thỏa thuận chung giữa frontend và backend về:

1. Frontend được gửi những dữ liệu và thao tác nào.
2. Backend phải validate và thực hiện logic gì.
3. Backend được đọc dữ liệu gì từ database.
4. Backend phải ghi dữ liệu gì vào database.
5. Frontend nhận lại response theo cấu trúc nào.
6. Trạng thái và lỗi được biểu diễn thống nhất ra sao.

Contract này giữ nguyên luồng import đang có:

```text
upload file
→ nhận diện sheet và cột
→ người dùng duyệt mapping
→ xác nhận mapping
→ backend xử lý và ghi database
→ frontend đọc lại dữ liệu chuẩn hóa
```

Đồng thời contract bổ sung các API còn thiếu để dữ liệu kho, công thức, lịch sử,
dự báo, kế hoạch nhập và đơn nhập tồn tại sau khi tải lại trang.

---

## 2. Kiến trúc và URL frontend phải gọi

### 2.1. Luồng request

```text
React trong trình duyệt
→ Node.js proxy cùng domain
→ ShelfCash backend
→ Database / Qwen / Forecast engine
```

Ví dụ backend thật:

```text
https://example.trycloudflare.com
```

React không gọi trực tiếp URL này. React gọi:

```text
/api/shelfcash/api/v1/...
```

Node.js proxy đọc:

```dotenv
SHELFCASH_BACKEND_URL=https://example.trycloudflare.com
SHELFCASH_API_KEY=...
```

Sau đó proxy chuyển tiếp request đến backend thật và tự gắn API key.

### 2.2. Ví dụ chuyển tiếp

React gọi:

```http
POST /api/shelfcash/api/v1/imports
```

Node.js chuyển thành:

```http
POST https://example.trycloudflare.com/api/v1/imports
X-ShelfCash-Key: <secret>
```

API key không được đặt trong React, không được đặt trong biến `NEXT_PUBLIC_*`,
và không được xuất hiện trong tab Network của trình duyệt.

---

## 3. Ranh giới trách nhiệm

### 3.1. Frontend được gửi

Frontend được gửi những dữ liệu người dùng thật sự nhập hoặc lựa chọn:

- File Excel/CSV.
- `store_id`.
- Ngày dự báo và số ngày dự báo.
- Mapping cột do người dùng sửa.
- Số lượng kiểm kho thực tế.
- Thông tin lô hàng, hạn sử dụng và hao hụt.
- Công thức/Bill of Materials được chỉnh sửa.
- Ngân sách, lịch lễ, lịch khuyến mãi.
- Chiến lược nhập hàng: `economy`, `balanced`, `safe`.
- Số lượng đơn nhập được người dùng điều chỉnh.
- Hành động xác nhận Draft Purchase Order.

### 3.2. Frontend không được quyết định

Các giá trị sau phải do backend tính lại, không tin trực tiếp dữ liệu do
trình duyệt gửi:

- P25, P50, P75.
- Trạng thái `stockout`, `low`, `expiring`, `overstock`.
- `days_supply`.
- Lượng tồn khả dụng.
- Nhu cầu nguyên liệu quy đổi từ doanh số và recipe.
- Lượng nhập đề xuất.
- Làm tròn theo MOQ và pack size.
- Tổng tiền đơn nhập.
- Ngân sách còn lại.
- Cảnh báo sức chứa.
- Rủi ro hết hạn.
- Ngày giao dự kiến.
- Quyền truy cập vào một `store_id`.

Frontend có thể gửi số lượng override, nhưng backend phải tính lại mọi tổng tiền
và cảnh báo trước khi lưu.

### 3.3. Backend chịu trách nhiệm

- Xác thực request và quyền truy cập cửa hàng.
- Validate kiểu dữ liệu, đơn vị, ngày tháng và giá trị âm.
- Chuẩn hóa tên nguyên liệu/sản phẩm qua alias.
- Đảm bảo thao tác lặp lại không tạo dữ liệu trùng.
- Ghi dữ liệu trong transaction; lỗi giữa chừng không được ghi một nửa.
- Lưu lịch sử thay đổi và nguồn dữ liệu.
- Chạy demand reconstruction, forecast, calibration và planning.
- Đọc trạng thái hiện tại từ database thay vì tin snapshot do frontend gửi.
- Trả lỗi theo một cấu trúc thống nhất.

---

## 4. Quy ước chung

### 4.1. Tên field

- JSON sử dụng `snake_case`.
- React có thể đổi sang `camelCase` trong adapter nội bộ, nhưng JSON qua API
  không được thay đổi tên.
- ID sử dụng UUID string.

### 4.2. Ngày, giờ và tiền

- Ngày: `YYYY-MM-DD`.
- Thời điểm: ISO 8601 có timezone, ví dụ `2026-07-28T17:30:00+07:00`.
- Timezone mặc định của cửa hàng: `Asia/Ho_Chi_Minh`.
- Tiền VND dùng số nguyên, ví dụ `32000`, không gửi chuỗi `"32.000 đ"`.
- Số lượng dùng JSON number và mặc định phải lớn hơn hoặc bằng 0; chỉ field
  delta như `quantity_delta` được phép âm theo semantics của endpoint.

### 4.3. Đơn vị chuẩn trong MVP

```text
kg | g | lít | ml | cái
```

Backend có thể nhận alias như `l`, `liter`, `litre` trong file import, nhưng phải
chuẩn hóa trước khi ghi database.

### 4.4. Header

Request JSON:

```http
Accept: application/json
Content-Type: application/json
```

Request từ Node proxy đến backend:

```http
X-ShelfCash-Key: <secret>
```

Các thao tác có nguy cơ tạo dữ liệu trùng nên hỗ trợ:

```http
Idempotency-Key: <uuid-generated-by-client>
```

Áp dụng ít nhất cho:

- `POST /imports`
- `POST /forecast-runs`
- `POST /plan-runs`
- `POST /purchase-orders`
- `POST /inventory-counts`
- `POST /inventory-adjustments`
- `POST /sales-history/batch`
- `POST /purchase-history/batch`
- `POST /purchase-orders/{po_id}/receive`

Với các endpoint bắt buộc idempotency:

- Cùng `Idempotency-Key` và cùng body: backend trả lại response đã lưu của lần
  thành công trước, không tạo side effect mới.
- Cùng `Idempotency-Key` nhưng body khác: trả `409 DUPLICATE_REQUEST`.
- Các batch write là transaction all-or-nothing.
- Public write API từ chối field ngoài schema bằng `422 VALIDATION_ERROR`; không
  âm thầm bỏ qua field lạ.

### 4.5. Phân trang

Request:

```text
?page=1&page_size=50
```

Response:

```json
{
  "items": [],
  "page": 1,
  "page_size": 50,
  "total": 0
}
```

### 4.6. Error JSON

```json
{
  "code": "VALIDATION_ERROR",
  "message": "Dữ liệu không hợp lệ.",
  "details": {
    "fields": [
      {
        "field": "quantity",
        "reason": "must_be_greater_than_zero"
      }
    ]
  },
  "request_id": "req_01K..."
}
```

`request_id` có thể bỏ qua trong MVP, nhưng nên có để tra log.

| HTTP status | Ý nghĩa |
|---|---|
| `200` | Đọc hoặc cập nhật thành công |
| `201` | Tạo resource thành công |
| `202` | Đã nhận job, backend đang xử lý |
| `400` | Request sai logic |
| `401` | Thiếu hoặc sai API key/token |
| `403` | Không có quyền với cửa hàng |
| `404` | Không tìm thấy resource |
| `409` | Trùng dữ liệu, xung đột version hoặc sai trạng thái |
| `413` | File quá lớn |
| `422` | Body đúng JSON nhưng không đúng schema |
| `425` | Kết quả chưa sẵn sàng |
| `500` | Lỗi backend |
| `502` | Node proxy không gọi được backend |
| `503` | Backend/model chưa sẵn sàng |

Các mã lỗi chính:

```text
VALIDATION_ERROR
UNAUTHORIZED
FORBIDDEN
STORE_NOT_FOUND
RESOURCE_NOT_FOUND
VERSION_CONFLICT
DUPLICATE_REQUEST
IMPORT_NOT_FOUND
IMPORT_NOT_READY
IMPORT_PROCESSING
MAPPING_INCOMPLETE
MODEL_NOT_READY
INSUFFICIENT_HISTORY
BUDGET_EXCEEDED
INVALID_STATE_TRANSITION
BACKEND_UNREACHABLE
BACKEND_NOT_CONFIGURED
```

### 4.7. Trạng thái Purchase Order và ngân sách

PO trong contract v1 sử dụng state machine:

```text
draft → ordered → partially_received → received
```

- Chỉ PO `draft` được sửa line.
- Confirm chuyển `draft → ordered`.
- Nhận chưa đủ chuyển `ordered/partially_received → partially_received`.
- Nhận đủ mọi line chuyển thành `received`.
- Không được chuyển lùi trạng thái.

Policy ngân sách chính thức:

```text
remaining_budget
= monthly_budget - reserved_budget - spent_budget
```

- Draft chỉ hiển thị preview, chưa tác động ngân sách.
- Confirm reserve toàn bộ giá trị PO.
- Receive chuyển chi phí phần đã nhận từ `reserved_budget` sang
  `spent_budget`; không trừ `remaining_budget` lần hai nếu giá nhận đúng giá đã
  khóa trên PO.
- Mọi thay đổi ngân sách và trạng thái PO phải nằm cùng transaction với
  operation tương ứng.

---

## 5. Các schema dữ liệu chuẩn

### 5.1. Inventory lot

```json
{
  "lot_id": "0d907b73-d293-4de9-8db1-cfca28cde92b",
  "ingredient_id": "b481314a-2720-46bd-a3dd-6d83cc16fd4d",
  "ingredient": "Sữa tươi",
  "sku": "NL-SUA-001",
  "on_hand": 7,
  "usable_quantity": 5,
  "expiring_quantity": 2,
  "unit": "lít",
  "unit_cost": 32000,
  "received_date": "2026-07-25",
  "received_date_status": "declared",
  "expiry_date": "2026-08-03",
  "supplier_id": "654175b2-d265-46c9-ad2b-cab520e7c29c",
  "supplier": "ABC Food",
  "status": "expiring",
  "snapshot_date": "2026-07-28",
  "version": 3
}
```

`on_hand`, `usable_quantity`, `expiring_quantity` và `status` do backend tính.

### 5.2. Product và recipe version

```json
{
  "product_id": "106ef881-1b84-4403-8082-c38ef2fbb9ea",
  "product": "Sinh tố chuối",
  "sku": "SP-STC-001",
  "price": 35000,
  "active_recipe": {
    "recipe_version_id": "129471f7-a087-47d7-8e7e-193db6862ba7",
    "effective_from": "2026-07-28",
    "lines": [
      {
        "ingredient_id": "1ffdd236-e409-4c2c-a852-4da8240e736f",
        "ingredient": "Chuối",
        "quantity": 0.12,
        "unit": "kg"
      },
      {
        "ingredient_id": "b481314a-2720-46bd-a3dd-6d83cc16fd4d",
        "ingredient": "Sữa tươi",
        "quantity": 0.15,
        "unit": "lít"
      }
    ]
  }
}
```

Khi sửa recipe, backend tạo version mới. Không ghi đè version cũ.

### 5.3. Sales history

```json
{
  "sales_record_id": "6eb52a91-0b23-4ff3-a86d-acad5ff40662",
  "date": "2026-07-27",
  "product_id": "106ef881-1b84-4403-8082-c38ef2fbb9ea",
  "product": "Sinh tố chuối",
  "quantity": 42,
  "unit_price": 35000,
  "promotion": false,
  "source": "import",
  "import_id": "70d4389e-7af3-464b-a5cb-c9b21bd5ee48"
}
```

### 5.4. Usage history

```json
{
  "usage_record_id": "a537aaf5-470f-4b0e-8bdd-e648c72c0bb8",
  "date": "2026-07-27",
  "ingredient_id": "b481314a-2720-46bd-a3dd-6d83cc16fd4d",
  "ingredient": "Sữa tươi",
  "quantity": 6.3,
  "unit": "lít",
  "source": "reconstructed_from_sales"
}
```

### 5.5. Supplier constraint

```json
{
  "constraint_id": "99ec5707-5561-47bb-b1f0-1094818bf6ad",
  "ingredient_id": "b481314a-2720-46bd-a3dd-6d83cc16fd4d",
  "supplier_id": "654175b2-d265-46c9-ad2b-cab520e7c29c",
  "supplier": "ABC Food",
  "unit_cost": 32000,
  "moq": 12,
  "pack_size": 12,
  "lead_time_days": 2,
  "unit": "lít",
  "version": 2
}
```

### 5.6. Forecast point

```json
{
  "date": "2026-07-29",
  "ingredient_id": "b481314a-2720-46bd-a3dd-6d83cc16fd4d",
  "ingredient": "Sữa tươi",
  "unit": "lít",
  "p25": 5.1,
  "p50": 6.4,
  "p75": 7.8,
  "promotion": false,
  "weekend": false
}
```

Backend phải bảo đảm:

```text
p25 <= p50 <= p75
```

### 5.7. Recommendation

```json
{
  "recommendation_id": "223921dd-944b-4f8d-bac5-bcd65b8661cd",
  "ingredient_id": "b481314a-2720-46bd-a3dd-6d83cc16fd4d",
  "ingredient": "Sữa tươi",
  "unit": "lít",
  "on_hand": 7,
  "usable_stock": 5,
  "forecast_demand": 18.4,
  "safety_stock": 4,
  "inbound": 0,
  "raw_recommended_quantity": 17.4,
  "order_quantity": 24,
  "unit_cost": 32000,
  "cost": 768000,
  "supplier_id": "654175b2-d265-46c9-ad2b-cab520e7c29c",
  "supplier": "ABC Food",
  "moq": 12,
  "pack_size": 12,
  "lead_time_days": 2,
  "expiry_risk_quantity": 2,
  "capacity_warning": false,
  "reason_codes": [
    "BELOW_SAFETY_STOCK",
    "ROUNDED_TO_PACK_SIZE"
  ]
}
```

---

## 6. Danh sách endpoint

Ký hiệu:

- **P0:** cần làm để MVP hiện tại lưu và đọc database thật.
- **P1:** nên làm sau khi P0 ổn định.
- **Có sẵn:** frontend hiện đã gọi endpoint này.

### 6.1. Health và LLM

| Priority | Method | Endpoint | Mục đích |
|---|---|---|---|
| Có sẵn | `GET` | `/health` | Health backend |
| Có sẵn | `GET` | `/api/v1/llm/health` | Health Qwen/provider |
| Có sẵn | `POST` | `/api/v1/llm/map-sheet` | Gợi ý mapping cho một sheet |

### 6.2. Import

| Priority | Method | Endpoint | Mục đích |
|---|---|---|---|
| Có sẵn | `POST` | `/api/v1/imports` | Upload nhiều file |
| Có sẵn | `GET` | `/api/v1/imports/{import_id}` | Đọc trạng thái import |
| Có sẵn | `POST` | `/api/v1/imports/{import_id}/confirm` | Xác nhận mapping |
| Có sẵn | `POST` | `/api/v1/imports/{import_id}/process` | Chuẩn hóa và ghi database |
| Có sẵn | `GET` | `/api/v1/imports/{import_id}/result` | Lấy kết quả chuẩn hóa |
| P1 | `GET` | `/api/v1/stores/{store_id}/imports` | Lịch sử import |

### 6.3. Khởi tạo giao diện và dashboard

| Priority | Method | Endpoint | Mục đích |
|---|---|---|---|
| P0 | `GET` | `/api/v1/stores/{store_id}/bootstrap` | Nạp dữ liệu ban đầu sau khi mở/tải lại trang |
| P0 | `GET` | `/api/v1/stores/{store_id}/dashboard` | KPI, cảnh báo và độ mới dữ liệu |

### 6.4. Kho

| Priority | Method | Endpoint | Mục đích |
|---|---|---|---|
| P0 | `GET` | `/api/v1/stores/{store_id}/inventory` | Đọc tồn kho theo lô |
| P0 | `POST` | `/api/v1/stores/{store_id}/inventory-counts` | Ghi lần kiểm kho thực tế |
| P0 | `POST` | `/api/v1/stores/{store_id}/inventory-adjustments` | Ghi hao hụt, hết hạn hoặc điều chỉnh |
| P1 | `GET` | `/api/v1/stores/{store_id}/inventory-movements` | Lịch sử biến động kho |

### 6.5. Nguyên liệu, sản phẩm và recipe

| Priority | Method | Endpoint | Mục đích |
|---|---|---|---|
| P0 | `GET` | `/api/v1/stores/{store_id}/ingredients` | Danh sách nguyên liệu |
| P0 | `POST` | `/api/v1/stores/{store_id}/ingredients` | Tạo nguyên liệu |
| P0 | `PATCH` | `/api/v1/stores/{store_id}/ingredients/{ingredient_id}` | Sửa thông tin nguyên liệu |
| P0 | `GET` | `/api/v1/stores/{store_id}/products` | Danh sách sản phẩm |
| P0 | `POST` | `/api/v1/stores/{store_id}/products` | Tạo sản phẩm |
| P0 | `PATCH` | `/api/v1/stores/{store_id}/products/{product_id}` | Sửa sản phẩm |
| P0 | `GET` | `/api/v1/stores/{store_id}/products/{product_id}/recipe` | Recipe đang có hiệu lực |
| P0 | `PUT` | `/api/v1/stores/{store_id}/products/{product_id}/recipe` | Tạo recipe version mới |
| P1 | `GET` | `/api/v1/stores/{store_id}/products/{product_id}/recipe-versions` | Lịch sử recipe |

### 6.6. Dữ liệu lịch sử

| Priority | Method | Endpoint | Mục đích |
|---|---|---|---|
| P0 | `GET` | `/api/v1/stores/{store_id}/sales-history` | Đọc lịch sử bán |
| P0 | `GET` | `/api/v1/stores/{store_id}/usage-history` | Đọc lịch sử tiêu thụ |
| P0 | `GET` | `/api/v1/stores/{store_id}/purchase-history` | Đọc lịch sử nhập |
| P1 | `POST` | `/api/v1/stores/{store_id}/sales-history/batch` | Ghi batch doanh số POS; không tự trừ tồn kho |
| P1 | `POST` | `/api/v1/stores/{store_id}/purchase-history/batch` | Backfill lịch sử dạng `record_only`; không nhận hàng vào kho |

### 6.7. Nhà cung cấp, alias và cài đặt

| Priority | Method | Endpoint | Mục đích |
|---|---|---|---|
| P0 | `GET` | `/api/v1/stores/{store_id}/supplier-constraints` | Đọc MOQ, pack size, lead time |
| P0 | `POST` | `/api/v1/stores/{store_id}/supplier-constraints` | Tạo quy tắc |
| P0 | `PUT` | `/api/v1/stores/{store_id}/supplier-constraints/{constraint_id}` | Sửa quy tắc |
| P0 | `GET` | `/api/v1/stores/{store_id}/aliases` | Đọc tên thay thế |
| P0 | `PUT` | `/api/v1/stores/{store_id}/aliases` | Ghi danh sách alias |
| P0 | `GET` | `/api/v1/stores/{store_id}/settings` | Đọc cài đặt |
| P0 | `PUT` | `/api/v1/stores/{store_id}/settings` | Cập nhật ngân sách/cấu hình |
| P0 | `GET` | `/api/v1/stores/{store_id}/calendar-features` | Đọc lịch |
| P0 | `PUT` | `/api/v1/stores/{store_id}/calendar-features` | Upsert lịch lễ/khuyến mãi |

### 6.8. Forecast, plan và Purchase Order

| Priority | Method | Endpoint | Mục đích |
|---|---|---|---|
| P0 | `POST` | `/api/v1/stores/{store_id}/forecast-runs` | Tạo forecast job |
| P0 | `GET` | `/api/v1/stores/{store_id}/forecast-runs/{forecast_run_id}` | Đọc trạng thái job |
| P0 | `GET` | `/api/v1/stores/{store_id}/forecast-runs/{forecast_run_id}/result` | Lấy P25/P50/P75 |
| P0 | `POST` | `/api/v1/stores/{store_id}/plan-runs` | Tạo kế hoạch nhập |
| P0 | `GET` | `/api/v1/stores/{store_id}/plan-runs/{plan_run_id}` | Đọc trạng thái plan |
| P0 | `GET` | `/api/v1/stores/{store_id}/plan-runs/{plan_run_id}/result` | Lấy recommendations |
| P0 | `POST` | `/api/v1/stores/{store_id}/purchase-orders` | Tạo Draft PO từ plan |
| P0 | `GET` | `/api/v1/stores/{store_id}/purchase-orders` | Danh sách PO |
| P0 | `GET` | `/api/v1/stores/{store_id}/purchase-orders/{po_id}` | Chi tiết PO |
| P0 | `PATCH` | `/api/v1/stores/{store_id}/purchase-orders/{po_id}` | Sửa Draft PO |
| P0 | `POST` | `/api/v1/stores/{store_id}/purchase-orders/{po_id}/confirm` | Xác nhận đã đặt |
| P1 | `POST` | `/api/v1/stores/{store_id}/purchase-orders/{po_id}/receive` | Nhận một phần/toàn bộ PO vào tồn kho và chuyển reserved sang spent |

---

## 7. Contract chi tiết cho các luồng chính

## 7.1. Health backend

### Request

```http
GET /health
```

### Response `200`

```json
{
  "status": "ok",
  "service": "shelfcash-backend",
  "version": "1.0.0"
}
```

Không đọc hoặc ghi database bắt buộc. Có thể kiểm tra kết nối database và trả
thêm:

```json
{
  "database": "ready"
}
```

---

## 7.2. Tạo import

### Request

```http
POST /api/v1/imports
Content-Type: multipart/form-data
```

Form fields:

| Field | Kiểu | Bắt buộc | Ý nghĩa |
|---|---|---|---|
| `files` | `File[]` | Có | Excel/CSV |
| `store_id` | string | Có | Cửa hàng nhận dữ liệu |
| `forecast_date` | date | Không | Mặc định ngày hiện tại |
| `forecast_horizon` | integer | Không | Mặc định `7`, từ `1` đến `90` |

Giới hạn đề xuất cho MVP:

- Tối đa 10 file/request.
- Tối đa 12 MB/file.
- Tổng tối đa 50 MB.
- Chấp nhận `.xlsx`, `.xls`, `.csv`.

### Backend logic

1. Kiểm tra `store_id`.
2. Lưu metadata file và checksum.
3. Đọc workbook/sheet.
4. Tạo `SheetProfile`.
5. Chạy rule mapping.
6. Chỉ gọi Qwen khi rule không đủ confidence.
7. Lưu profile, suggestion, warning và error.
8. Không ghi dữ liệu nghiệp vụ vào các bảng kho/bán hàng ở bước này.

### Database

Đọc:

- `stores`
- `ingredient_aliases`
- danh mục field/schema được hỗ trợ

Ghi:

- `import_jobs`
- `import_files`
- `import_sheet_profiles`
- `import_mappings`
- `import_issues`

### Response `201`

```json
{
  "import_id": "70d4389e-7af3-464b-a5cb-c9b21bd5ee48",
  "status": "mapping_required",
  "profiles": [
    {
      "profile_id": "5887964a-cae4-47c4-9138-f12d4498ff12",
      "file_name": "inventory.xlsx",
      "sheet_name": "Kho",
      "row_count": 23,
      "columns": [
        "Tên nguyên liệu",
        "Số lượng",
        "Đơn vị",
        "HSD"
      ],
      "sample_rows": [
        {
          "Tên nguyên liệu": "Sữa tươi",
          "Số lượng": 7,
          "Đơn vị": "lít",
          "HSD": "2026-08-03"
        }
      ]
    }
  ],
  "suggested_mappings": [
    {
      "profile_id": "5887964a-cae4-47c4-9138-f12d4498ff12",
      "sheet_name": "Kho",
      "sheet_type": "inventory",
      "column_mapping": {
        "Tên nguyên liệu": "ingredient",
        "Số lượng": "on_hand",
        "Đơn vị": "unit",
        "HSD": "expiry_date"
      },
      "confidence": 0.94,
      "source": "rule"
    }
  ],
  "warnings": [],
  "errors": [],
  "requires_review": true
}
```

---

## 7.3. Xác nhận mapping

### Request

```http
POST /api/v1/imports/{import_id}/confirm
Content-Type: application/json
```

```json
{
  "mappings": [
    {
      "profile_id": "5887964a-cae4-47c4-9138-f12d4498ff12",
      "sheet_name": "Kho",
      "sheet_type": "inventory",
      "column_mapping": {
        "Tên nguyên liệu": "ingredient",
        "Số lượng": "on_hand",
        "Đơn vị": "unit",
        "HSD": "expiry_date"
      }
    }
  ]
}
```

### Backend logic

- Kiểm tra `profile_id` thuộc import.
- Kiểm tra `sheet_type` hợp lệ.
- Kiểm tra đủ field bắt buộc.
- Không cho hai cột nguồn map vào cùng field đích nếu schema không cho phép.
- Lưu mapping đã confirm.

### Response `200`

```json
{
  "import_id": "70d4389e-7af3-464b-a5cb-c9b21bd5ee48",
  "status": "confirmed",
  "requires_review": false,
  "warnings": [],
  "errors": []
}
```

---

## 7.4. Process import và ghi database

### Request

```http
POST /api/v1/imports/{import_id}/process
```

Không cần body để tương thích frontend hiện tại.

### Backend logic

1. Chỉ xử lý import ở trạng thái `confirmed`.
2. Chuẩn hóa kiểu số, ngày, boolean và đơn vị.
3. Resolve alias về ingredient/product chuẩn.
4. Validate quan hệ recipe, supplier và store.
5. Tính `source_row_hash` để tránh ghi trùng.
6. Ghi toàn bộ dữ liệu hợp lệ trong một transaction.
7. Nếu có lỗi nghiêm trọng, rollback toàn bộ.
8. Nếu request được gọi lại với cùng `import_id`, không tạo bản ghi trùng.

### Database ghi theo sheet type

| `sheet_type` | Bảng đích |
|---|---|
| `inventory` | `inventory_lots`, `inventory_movements` |
| `sales_history` | `sales_daily` |
| `usage_history` | `usage_daily` |
| `recipes` | `recipe_versions`, `recipe_lines` |
| `purchase_history` | `purchase_receipts` |
| `supplier_constraints` | `supplier_ingredient_terms` |
| `calendar_features` | `calendar_features` |
| `business_constraints` | `store_settings` |

### Response

Nếu xử lý đồng bộ và nhanh:

```http
200 OK
```

Nếu chạy background job:

```http
202 Accepted
```

```json
{
  "import_id": "70d4389e-7af3-464b-a5cb-c9b21bd5ee48",
  "status": "processing",
  "progress": {
    "processed_rows": 700,
    "total_rows": 2300
  }
}
```

Trạng thái import:

```text
uploaded
→ mapping_required
→ confirmed
→ processing
→ completed | failed
```

---

## 7.5. Lấy kết quả import

### Request

```http
GET /api/v1/imports/{import_id}/result
```

### Response `200`

```json
{
  "store_id": "STORE_001",
  "forecast_date": "2026-07-28",
  "forecast_horizon": 7,
  "inventory": [],
  "sales_history": [],
  "usage_history": [],
  "recipes": [],
  "purchase_history": [],
  "supplier_constraints": [],
  "calendar_features": [],
  "business_constraints": [],
  "validation_summary": {
    "total_rows": 2300,
    "accepted_rows": 2280,
    "rejected_rows": 20,
    "warning_rows": 31
  },
  "ingestion_metadata": {
    "import_id": "70d4389e-7af3-464b-a5cb-c9b21bd5ee48",
    "status": "completed",
    "committed_at": "2026-07-28T18:00:00+07:00"
  }
}
```

Nếu chưa xong:

```http
425 Too Early
```

```json
{
  "code": "IMPORT_NOT_READY",
  "message": "Import vẫn đang được xử lý.",
  "details": {
    "status": "processing"
  }
}
```

---

## 7.6. Bootstrap: đọc dữ liệu khi mở lại frontend

Đây là endpoint quan trọng để sửa nhược điểm “reload trang là mất dữ liệu”.

### Request

```http
GET /api/v1/stores/STORE_001/bootstrap
```

### Database đọc

- `stores`
- `store_settings`
- `ingredients`
- `products`
- recipe version đang hiệu lực
- `inventory_lots`
- `supplier_ingredient_terms`
- `ingredient_aliases`
- `calendar_features`
- forecast/plan/PO mới nhất

### Response `200`

```json
{
  "today": "2026-07-28",
  "store": {
    "store_id": "STORE_001",
    "store_name": "Cửa hàng Quận 3",
    "timezone": "Asia/Ho_Chi_Minh",
    "currency": "VND"
  },
  "inventory": [],
  "products": [],
  "recipes": [],
  "supplier_constraints": [],
  "aliases": [],
  "future_calendar": [],
  "settings": {
    "monthly_budget": 5000000,
    "reserved_budget": 768000,
    "spent_budget": 1932000,
    "remaining_budget": 2300000,
    "forecast_horizon": 7
  },
  "latest_runs": {
    "forecast_run_id": null,
    "plan_run_id": null
  },
  "data_freshness": {
    "inventory_updated_at": "2026-07-28T09:00:00+07:00",
    "sales_max_date": "2026-07-27",
    "recipes_updated_at": "2026-07-26T11:00:00+07:00"
  }
}
```

Frontend gọi endpoint này khi:

- Mở ứng dụng.
- Tải lại trang.
- Đổi cửa hàng.
- Import hoàn tất.
- PO được xác nhận hoặc hàng được nhận.

---

## 7.7. Ghi nhận kiểm kho

Frontend không nên PATCH thẳng `on_hand`. Nó gửi một lần kiểm đếm thực tế để
backend tạo inventory movement có thể truy vết.

### Request

```http
POST /api/v1/stores/STORE_001/inventory-counts
Content-Type: application/json
Idempotency-Key: 280d1c02-73c3-4a69-844f-fe1cfc0a18a1
```

```json
{
  "counted_at": "2026-07-28T18:30:00+07:00",
  "lines": [
    {
      "lot_id": "0d907b73-d293-4de9-8db1-cfca28cde92b",
      "counted_quantity": 6.5,
      "unit": "lít",
      "note": "Kiểm cuối ngày"
    }
  ]
}
```

### Backend logic

- Kiểm tra lot thuộc store.
- Chuẩn hóa đơn vị.
- So sánh số đếm và số tồn hệ thống.
- Tạo movement loại `physical_count_adjustment`.
- Tính lại tồn kho và trạng thái.
- Không cho tồn sau điều chỉnh âm.

### Response `201`

```json
{
  "inventory_count_id": "bf613125-95c0-43db-b97d-b21cc4118092",
  "adjustments": [
    {
      "lot_id": "0d907b73-d293-4de9-8db1-cfca28cde92b",
      "before_quantity": 7,
      "counted_quantity": 6.5,
      "difference": -0.5
    }
  ],
  "inventory": []
}
```

---

## 7.8. Sửa recipe

### Request

```http
PUT /api/v1/stores/STORE_001/products/{product_id}/recipe
Content-Type: application/json
```

```json
{
  "effective_from": "2026-07-29",
  "version": 2,
  "lines": [
    {
      "ingredient_id": "1ffdd236-e409-4c2c-a852-4da8240e736f",
      "quantity": 0.12,
      "unit": "kg"
    },
    {
      "ingredient_id": "b481314a-2720-46bd-a3dd-6d83cc16fd4d",
      "quantity": 0.15,
      "unit": "lít"
    }
  ]
}
```

### Backend logic

- Product và ingredient phải thuộc cùng store.
- Không cho lặp ingredient trong cùng recipe.
- Mọi quantity phải lớn hơn 0.
- Kiểm tra đơn vị tương thích.
- Kết thúc hiệu lực version cũ vào ngày trước `effective_from`.
- Tạo version mới.
- Không sửa lịch sử recipe đã dùng để reconstruct demand cũ.

### Response `200`

Trả product cùng `active_recipe` hoặc `scheduled_recipe`.

Nếu `version` cũ hơn database:

```http
409 Conflict
```

```json
{
  "code": "VERSION_CONFLICT",
  "message": "Recipe đã được người khác cập nhật.",
  "details": {
    "expected_version": 2,
    "current_version": 3
  }
}
```

---

## 7.9. Chạy forecast

Frontend chỉ gửi phạm vi và bối cảnh. Backend tự lấy lịch sử từ database.

### Request

```http
POST /api/v1/stores/STORE_001/forecast-runs
Content-Type: application/json
Idempotency-Key: 4f83fc55-45e7-4106-b12e-1bb5851d1e6a
```

```json
{
  "cutoff_date": "2026-07-28",
  "horizon_days": 7,
  "quantiles": [0.25, 0.5, 0.75],
  "scope": {
    "ingredient_ids": []
  },
  "use_latest_calendar": true
}
```

Mảng `ingredient_ids` rỗng nghĩa là forecast toàn bộ nguyên liệu đang hoạt động.

### Backend đọc database

- `sales_daily`
- `usage_daily`
- `products`
- recipe version đúng theo từng thời điểm
- `calendar_features`
- `stores`
- `model_registry`
- `calibrators`

### Backend logic

1. Chọn dữ liệu đến đúng `cutoff_date`; không dùng dữ liệu tương lai.
2. Ưu tiên usage thật.
3. Nếu thiếu usage, reconstruct demand từ sales và recipe version lịch sử.
4. Tạo leakage-safe features.
5. Chạy baseline và LightGBM quantile.
6. Sửa quantile crossing.
7. Áp dụng horizon calibrator/CQR.
8. Lưu run, model version và forecast points.

### Response `202`

```json
{
  "forecast_run_id": "2b2dd3d4-d99d-4879-8f84-cdfcae3602e0",
  "status": "queued",
  "cutoff_date": "2026-07-28",
  "horizon_days": 7
}
```

### Result response

```json
{
  "forecast_run_id": "2b2dd3d4-d99d-4879-8f84-cdfcae3602e0",
  "status": "completed",
  "model_version": "lgbm-quantile-2026-07-28",
  "calibrator_version": "cqr-horizon-2026-07-28",
  "forecasts": [
    {
      "ingredient_id": "b481314a-2720-46bd-a3dd-6d83cc16fd4d",
      "ingredient": "Sữa tươi",
      "unit": "lít",
      "history": [],
      "forecast": [],
      "totals": {
        "p25": 35.2,
        "p50": 42.5,
        "p75": 51.1
      },
      "drivers": [
        "Cuối tuần",
        "Khuyến mãi Sinh tố chuối"
      ],
      "confidence": "good",
      "data_notes": []
    }
  ]
}
```

---

## 7.10. Tạo kế hoạch nhập

### Request

```http
POST /api/v1/stores/STORE_001/plan-runs
Content-Type: application/json
```

```json
{
  "forecast_run_id": "2b2dd3d4-d99d-4879-8f84-cdfcae3602e0",
  "strategy": "balanced",
  "budget_limit": 2300000,
  "as_of_date": "2026-07-28",
  "include_open_purchase_orders": true
}
```

Chiến lược:

```text
economy  → thiên về P25
balanced → thiên về P50
safe     → thiên về P75
```

`budget_limit` là trần chi tiêu riêng cho lần lập kế hoạch. Backend phải lấy
`remaining_budget` hiện tại từ database và sử dụng giá trị nhỏ hơn giữa hai
trần; frontend không thể dùng field này để nới ngân sách cửa hàng.

### Backend đọc database

- `forecast_points`
- `inventory_lots`
- `inventory_movements`
- `supplier_ingredient_terms`
- `store_settings`
- PO chưa nhận hàng
- `calendar_features`

### Backend logic

1. Chọn quantile theo strategy.
2. Tính usable stock theo FEFO và hạn sử dụng.
3. Trừ lượng dự kiến hỏng/hết hạn trong horizon.
4. Cộng inbound từ PO hợp lệ.
5. Cộng safety stock.
6. Tính raw recommended quantity.
7. Làm tròn theo MOQ và pack size.
8. Kiểm tra capacity và lead time.
9. Tối ưu trong budget.
10. Lưu plan snapshot để kết quả có thể audit.

### Response `202`

```json
{
  "plan_run_id": "deaf1054-58e4-4c59-bc8b-9ece56b1f247",
  "status": "queued",
  "strategy": "balanced"
}
```

### Result response

```json
{
  "plan_run_id": "deaf1054-58e4-4c59-bc8b-9ece56b1f247",
  "status": "completed",
  "strategy": "balanced",
  "budget": {
    "limit": 2300000,
    "planned_cost": 1846000,
    "remaining_after_plan": 454000
  },
  "recommendations": [],
  "warnings": []
}
```

---

## 7.11. Tạo Draft Purchase Order

### Request

```http
POST /api/v1/stores/STORE_001/purchase-orders
Content-Type: application/json
Idempotency-Key: b92aa353-a276-425c-8607-a977ef8b9250
```

```json
{
  "plan_run_id": "deaf1054-58e4-4c59-bc8b-9ece56b1f247",
  "lines": [
    {
      "recommendation_id": "223921dd-944b-4f8d-bac5-bcd65b8661cd",
      "order_quantity_override": 24
    }
  ]
}
```

Frontend không gửi `total`, `delivery_date` hoặc `budget_after` như dữ liệu có
thẩm quyền.

### Backend logic

- Kiểm tra recommendation thuộc plan và store.
- Kiểm tra override theo MOQ/pack size.
- Lấy đơn giá mới nhất từ supplier constraint.
- Gom line theo supplier.
- Tính tổng tiền.
- Kiểm tra budget.
- Tính ngày giao.
- Tạo Draft PO.

### Response `201`

```json
{
  "orders": [
    {
      "po_id": "PO-20260728-001",
      "supplier_id": "654175b2-d265-46c9-ad2b-cab520e7c29c",
      "supplier": "ABC Food",
      "order_date": "2026-07-28",
      "delivery_date": "2026-07-30",
      "strategy": "balanced",
      "status": "draft",
      "lines": [
        {
          "po_line_id": "bd9f98b1-0f34-4f27-a1e7-dd006ef58a38",
          "ingredient_id": "b481314a-2720-46bd-a3dd-6d83cc16fd4d",
          "order_quantity": 24,
          "unit": "lít",
          "unit_cost": 32000,
          "line_total": 768000
        }
      ],
      "total": 768000,
      "budget_after": 1532000,
      "version": 1
    }
  ]
}
```

---

## 7.12. Xác nhận PO đã đặt

### Request

```http
POST /api/v1/stores/STORE_001/purchase-orders/PO-20260728-001/confirm
Content-Type: application/json
```

```json
{
  "version": 1,
  "confirmed_at": "2026-07-28T19:00:00+07:00"
}
```

### Backend logic

- Chỉ cho phép chuyển `draft → ordered`.
- Tính lại tổng tiền lần cuối.
- Kiểm tra `remaining_budget` và reserve toàn bộ PO trong cùng transaction.
- Đưa quantity vào inbound inventory.
- Ghi audit log.

### Response `200`

Trả PO có:

```json
{
  "status": "ordered",
  "version": 2,
  "budget": {
    "budget_period": "2026-07",
    "reserved_added": 768000,
    "reserved_after": 1536000,
    "spent_after": 1932000,
    "remaining_budget": 1532000
  }
}
```

Không được xác nhận cùng PO hai lần.
Mọi PO line trong response phải luôn có `po_line_id`.

---

## 8. Public write contract đã khóa sau audit

Các body và semantics trong phần này là normative: frontend và backend phải
triển khai đúng field, side effect, idempotency và state transition được mô tả.
`store_id` chỉ nằm trên URL, không lặp lại trong body.

### 8.1. Ma trận side effect

| Operation | Tác động tồn kho | Tác động ngân sách |
|---|---:|---:|
| Alias bulk upsert | Không | Không |
| Inventory adjustment | Có | Không |
| Sales-history batch | Không | Không |
| Purchase-history batch | Không | Không |
| Calendar PUT | Không | Không |
| Draft PO PATCH | Không | Chỉ tính preview |
| PO confirm | Tạo inbound, chưa tăng `on_hand` | Reserve toàn bộ PO |
| PO receive | Tăng `on_hand`, giảm inbound | Chuyển phần nhận từ reserved sang spent |

### 8.2. PUT `/aliases`

```http
PUT /api/v1/stores/STORE_001/aliases
Content-Type: application/json
```

```json
{
  "aliases": [
    {
      "source_name": "sua tuoi",
      "canonical_name": "Sữa tươi",
      "ingredient_id": "b481314a-2720-46bd-a3dd-6d83cc16fd4d"
    },
    {
      "source_name": "banana",
      "canonical_name": "Chuối",
      "ingredient_id": "1ffdd236-e409-4c2c-a852-4da8240e736f"
    }
  ]
}
```

`ingredient_id` có thể bỏ qua nếu `canonical_name` resolve duy nhất trong cửa
hàng.

Semantics:

- Body bắt buộc là wrapper `{"aliases": [...]}`.
- Additive bulk upsert, không phải full replacement.
- Business key là `(store_id, normalized_source_name)`.
- Alias không có trong request được giữ nguyên.
- Không xóa dữ liệu.
- Hai item trùng `source_name` sau normalize trong cùng request: `422`.
- Cùng business key và cùng target: no-op.
- Cùng business key nhưng target khác: update.
- `ingredient_id`, nếu có, phải thuộc store và khớp canonical target.
- Request order không ảnh hưởng kết quả.
- Operation idempotent và transaction all-or-nothing.

Response `200`:

```json
{
  "created_count": 1,
  "updated_count": 1,
  "unchanged_count": 0,
  "aliases": [
    {
      "source_name": "sua tuoi",
      "canonical_name": "Sữa tươi",
      "ingredient_id": "b481314a-2720-46bd-a3dd-6d83cc16fd4d"
    }
  ]
}
```

### 8.3. POST `/inventory-adjustments`

```http
POST /api/v1/stores/STORE_001/inventory-adjustments
Content-Type: application/json
Idempotency-Key: e4fd5e12-7fb4-427f-bb48-1375372bacdd
```

```json
{
  "occurred_at": "2026-07-28T20:00:00+07:00",
  "reference": "WASTE-20260728-CLOSE",
  "lines": [
    {
      "lot_id": "0d907b73-d293-4de9-8db1-cfca28cde92b",
      "expected_version": 3,
      "quantity_delta": -2,
      "unit": "lít",
      "reason": "waste",
      "note": "Sữa bị đổ cuối ca"
    }
  ]
}
```

`reference` và `note` có thể bỏ qua.

`reason` chỉ nhận:

```text
waste
expired
damaged
correction_increase
correction_decrease
other
```

Semantics:

- `quantity_delta` là số có dấu và không được bằng `0`.
- `waste`, `expired`, `damaged`, `correction_decrease`: delta phải âm.
- `correction_increase`: delta phải dương.
- `other`: có thể âm hoặc dương nhưng bắt buộc có `note`.
- `expected_version` phải khớp version hiện tại của lot.
- Không cho hai line cùng `lot_id` trong một request.
- Không cho `after_quantity < 0`.
- Backend tạo `inventory_movements`, cập nhật lot và tăng version.
- Không sửa sales, purchase history, PO hoặc ngân sách.

Response `201`:

```json
{
  "inventory_adjustment_id": "e4fd5e12-7fb4-427f-bb48-1375372bacdd",
  "occurred_at": "2026-07-28T20:00:00+07:00",
  "movements": [
    {
      "movement_id": "ca819565-f220-4d0e-a993-ce2fe1436740",
      "lot_id": "0d907b73-d293-4de9-8db1-cfca28cde92b",
      "before_quantity": 7,
      "quantity_delta": -2,
      "after_quantity": 5,
      "version": 4
    }
  ]
}
```

### 8.4. POST `/sales-history/batch`

```http
POST /api/v1/stores/STORE_001/sales-history/batch
Content-Type: application/json
Idempotency-Key: e255284d-9ad7-4bb2-b89c-a2216883da34
```

```json
{
  "source": "pos",
  "records": [
    {
      "external_record_id": "POS-20260727-STC-DAILY",
      "date": "2026-07-27",
      "product_id": "106ef881-1b84-4403-8082-c38ef2fbb9ea",
      "quantity": 42,
      "unit_price": 35000,
      "promotion": false
    }
  ]
}
```

`source` chỉ nhận:

```text
pos | manual | integration
```

Semantics:

- Mỗi record là tổng bán của một sản phẩm trong một ngày.
- `quantity >= 0`; số `0` hợp lệ để biểu diễn ngày không bán.
- `unit_price` là số nguyên VND và `>= 0`.
- Product phải tồn tại và thuộc store.
- Business key là `(store_id, date, product_id)`.
- Record cũ có cùng nội dung: no-op.
- Cùng business key nhưng nội dung khác: `409 DUPLICATE_REQUEST`; endpoint này
  không âm thầm ghi đè lịch sử.
- Backend rebuild `usage_daily` có source `reconstructed_from_sales`, dùng
  recipe version có hiệu lực tại ngày bán.
- Usage thực tế/imported không bị ghi đè.
- Thiếu recipe: vẫn lưu sales nhưng trả warning.
- Không tạo inventory movement và không giảm `on_hand`.

Response `201`:

```json
{
  "batch_id": "e255284d-9ad7-4bb2-b89c-a2216883da34",
  "created_count": 1,
  "unchanged_count": 0,
  "records": [
    {
      "external_record_id": "POS-20260727-STC-DAILY",
      "sales_record_id": "6eb52a91-0b23-4ff3-a86d-acad5ff40662",
      "status": "created"
    }
  ],
  "usage_rebuild": {
    "status": "completed",
    "warning_count": 0
  },
  "warnings": []
}
```

Sửa hoặc xóa lịch sử bán không nằm trong contract v1; cần correction endpoint
riêng.

### 8.5. POST `/purchase-history/batch`

Endpoint này chỉ dùng để backfill lịch sử. Nó không phải operation nhận hàng
vào kho.

```http
POST /api/v1/stores/STORE_001/purchase-history/batch
Content-Type: application/json
Idempotency-Key: 4867caa8-2e17-4a79-b53a-04778f184865
```

```json
{
  "source": "supplier_invoice",
  "inventory_effect": "record_only",
  "records": [
    {
      "external_record_id": "INV-20260725-001-L1",
      "date": "2026-07-25",
      "ingredient_id": "b481314a-2720-46bd-a3dd-6d83cc16fd4d",
      "supplier_id": "654175b2-d265-46c9-ad2b-cab520e7c29c",
      "quantity": 24,
      "unit": "lít",
      "unit_cost": 32000,
      "expiry_date": "2026-08-03",
      "supplier_lot_code": "MILK-250725-A"
    }
  ]
}
```

`source` chỉ nhận:

```text
supplier_invoice | manual | integration
```

Trong v1, `inventory_effect` chỉ nhận:

```text
record_only
```

`expiry_date` và `supplier_lot_code` có thể là `null` hoặc bỏ qua.

Semantics:

- `quantity > 0`.
- `unit_cost` là số nguyên VND và `>= 0`.
- Ingredient và supplier phải thuộc store.
- Dedupe bằng `(store_id, source, external_record_id)`.
- Cùng ID và cùng dữ liệu: no-op.
- Cùng ID nhưng dữ liệu khác: `409 DUPLICATE_REQUEST`.
- Chỉ ghi purchase history/receipt record và audit log.
- Không tạo inventory lot.
- Không tăng `on_hand`.
- Không tạo inbound.
- Không liên kết hoặc thay đổi PO.
- Không reserve hoặc deduct ngân sách.

Response `201`:

```json
{
  "batch_id": "4867caa8-2e17-4a79-b53a-04778f184865",
  "created_count": 1,
  "unchanged_count": 0,
  "inventory_applied": false,
  "records": [
    {
      "external_record_id": "INV-20260725-001-L1",
      "purchase_record_id": "69b94270-2925-43bc-9a4e-dd1cab017185",
      "status": "created"
    }
  ]
}
```

Nhận hàng thực tế từ PO bắt buộc dùng `/purchase-orders/{po_id}/receive`. Nếu
sau này cần nhận hàng không có PO, tạo endpoint `/inventory-receipts`; không
overload history API.

### 8.6. PUT `/calendar-features`

```http
PUT /api/v1/stores/STORE_001/calendar-features
Content-Type: application/json
```

```json
{
  "items": [
    {
      "date": "2026-09-02",
      "holiday": true,
      "promotion": false,
      "promotion_note": null
    },
    {
      "date": "2026-09-05",
      "holiday": false,
      "promotion": true,
      "promotion_note": "Combo cuối tuần"
    }
  ]
}
```

Semantics:

- Additive bulk upsert theo `(store_id, date)`, không phải full replacement.
- Ngày không xuất hiện trong request được giữ nguyên.
- Không xóa dữ liệu.
- Lặp cùng ngày trong một request: `422`.
- Cùng ngày và cùng dữ liệu: no-op.
- Cùng ngày nhưng khác dữ liệu: update.
- Thứ trong tuần và `weekend` do backend tính.
- Nếu `promotion=false`, backend chuẩn hóa `promotion_note` thành `null`.
- Request order không ảnh hưởng kết quả.
- Operation idempotent và transaction all-or-nothing.
- Không sửa forecast đã hoàn thành; forecast run mới sẽ đọc lịch mới.

Response `200`:

```json
{
  "created_count": 1,
  "updated_count": 1,
  "unchanged_count": 0,
  "items": [
    {
      "date": "2026-09-02",
      "weekday": "Thứ Tư",
      "weekend": false,
      "holiday": true,
      "promotion": false,
      "promotion_note": null
    }
  ]
}
```

### 8.7. PATCH `/purchase-orders/{po_id}`

Chỉ cho phép sửa số lượng line khi PO còn `draft`.

```http
PATCH /api/v1/stores/STORE_001/purchase-orders/PO-20260728-001
Content-Type: application/json
```

```json
{
  "version": 1,
  "line_updates": [
    {
      "po_line_id": "bd9f98b1-0f34-4f27-a1e7-dd006ef58a38",
      "order_quantity": 36
    }
  ]
}
```

Semantics:

- Chỉ trạng thái `draft` được PATCH.
- `version` bắt buộc khớp version hiện tại.
- `line_updates` là partial update; line không xuất hiện được giữ nguyên.
- Không cho lặp `po_line_id`.
- Không cho thêm hoặc xóa line trong v1.
- `order_quantity > 0`.
- Backend kiểm tra MOQ, pack size, capacity và budget.
- Backend dùng `unit_cost` đã lưu trên Draft PO để tính lại `total`.
- `budget_after` chỉ là preview; chưa reserve ngân sách.
- Backend tăng version đúng một lần sau transaction thành công.
- Frontend không được gửi `status`, `supplier_id`, `unit_cost`, `total`,
  `delivery_date` hoặc `budget_after`.

Response `200` trả toàn bộ PO mới:

```json
{
  "po_id": "PO-20260728-001",
  "status": "draft",
  "version": 2,
  "lines": [
    {
      "po_line_id": "bd9f98b1-0f34-4f27-a1e7-dd006ef58a38",
      "order_quantity": 36,
      "unit": "lít",
      "unit_cost": 32000,
      "line_total": 1152000
    }
  ],
  "total": 1152000,
  "budget_after": 1148000
}
```

PO khác `draft`: `409 INVALID_STATE_TRANSITION`. Version cũ:
`409 VERSION_CONFLICT`.

### 8.8. POST `/purchase-orders/{po_id}/receive`

Operation hỗ trợ nhận một phần và một PO line có nhiều lô/hạn sử dụng.

```http
POST /api/v1/stores/STORE_001/purchase-orders/PO-20260728-001/receive
Content-Type: application/json
Idempotency-Key: f77b9fc4-58cc-4e42-88d6-a649b8eeb353
```

```json
{
  "version": 2,
  "received_at": "2026-07-30T09:15:00+07:00",
  "delivery_reference": "DN-000184",
  "lines": [
    {
      "po_line_id": "bd9f98b1-0f34-4f27-a1e7-dd006ef58a38",
      "lots": [
        {
          "quantity": 12,
          "expiry_date": "2026-08-10",
          "supplier_lot_code": "MILK-250730-A"
        }
      ]
    }
  ]
}
```

`delivery_reference` và `supplier_lot_code` có thể bỏ qua.

Semantics:

- Chỉ nhận từ trạng thái `ordered` hoặc `partially_received`.
- `version` phải khớp PO hiện tại.
- `received_at >= confirmed_at`.
- Mỗi `quantity > 0`.
- Tổng lots của một line là lượng nhận lần này.
- Tổng đã nhận lũy kế không được vượt `order_quantity`.
- `expiry_date` không được trước ngày nhận theo timezone cửa hàng.
- `unit`, `ingredient_id`, `supplier_id` và `unit_cost` lấy từ PO; frontend
  không gửi.
- Trong một transaction, backend:
  1. Tạo `purchase_receipt`.
  2. Tạo `inventory_lots`.
  3. Tạo movement loại `purchase_receipt`.
  4. Tăng tồn kho.
  5. Giảm inbound còn lại.
  6. Cập nhật received quantity của PO line.
  7. Chuyển ngân sách tương ứng từ reserved sang spent.
  8. Tăng PO version.
- Chưa nhận đủ: trạng thái `partially_received`.
- Mọi line đã nhận đủ: trạng thái `received`.

Response `201`:

```json
{
  "receipt_id": "f77b9fc4-58cc-4e42-88d6-a649b8eeb353",
  "po_id": "PO-20260728-001",
  "status": "partially_received",
  "version": 3,
  "received_at": "2026-07-30T09:15:00+07:00",
  "lines": [
    {
      "po_line_id": "bd9f98b1-0f34-4f27-a1e7-dd006ef58a38",
      "ordered_quantity": 36,
      "received_this_time": 12,
      "received_total": 12,
      "remaining_quantity": 24,
      "inventory_lots": [
        {
          "lot_id": "6cac06a7-95f5-4654-8194-e03bf6f22f31",
          "quantity_received": 12,
          "on_hand": 12,
          "unit": "lít",
          "expiry_date": "2026-08-10"
        }
      ]
    }
  ],
  "budget": {
    "budget_period": "2026-07",
    "moved_from_reserved_to_spent": 384000,
    "reserved_after": 768000,
    "spent_after": 2316000,
    "remaining_budget": 1916000
  }
}
```

### 8.9. Policy ngân sách PO

Policy chính thức là **reserve khi confirm; ghi spent khi receive**.

```text
draft
→ không tác động ngân sách

confirm
→ reserved_budget += PO total
→ remaining_budget -= PO total

receive
→ reserved_budget -= received cost
→ spent_budget += received cost
→ remaining_budget không đổi nếu giá PO không đổi
```

Quy tắc:

- `budget_after` của Draft PO chỉ là preview.
- Confirm tính lại tổng, kiểm tra ngân sách và reserve trong cùng transaction.
- `budget_period` được khóa theo tháng của `confirmed_at` trong timezone cửa
  hàng.
- Receipt dùng `unit_cost` đã khóa trên PO; body receive không được sửa giá.
- Nhận một phần chỉ chuyển chi phí của phần nhận từ reserved sang spent.
- Confirm không thay đổi `on_hand`; nó chỉ tạo inbound.
- Receive mới chuyển inbound thành tồn kho thật.
- Nếu tương lai có cancel PO, phần reserved chưa nhận phải được release.

`settings` và `bootstrap` phải trả đủ:

```json
{
  "monthly_budget": 5000000,
  "reserved_budget": 768000,
  "spent_budget": 1932000,
  "remaining_budget": 2300000
}
```

---

## 9. Frontend có thể đọc gì từ database?

Frontend không truy cập database trực tiếp. Backend đọc database rồi chỉ trả dữ
liệu thuộc đúng cửa hàng và đúng endpoint.

| Màn hình frontend | API đọc | Dữ liệu backend đọc từ database |
|---|---|---|
| Hôm nay | `GET /bootstrap`, `GET /dashboard` | Store, settings, kho, cảnh báo, forecast/plan mới nhất, PO mở |
| Nhập dữ liệu | `GET /imports/{id}`, `GET /imports/{id}/result` | Import job, mapping, lỗi, các row đã chuẩn hóa |
| Kho | `GET /inventory` | Inventory lots, movements, supplier terms |
| Công thức | `GET /products`, `GET /products/{id}/recipe` | Products, recipe versions, recipe lines |
| Kế hoạch nhập | `GET /forecast-runs/{id}/result`, `GET /plan-runs/{id}/result` | Forecast points, recommendations, budget snapshot |
| Draft PO | `GET /purchase-orders` | PO và PO lines |
| Nhà cung cấp | `GET /supplier-constraints` | Suppliers, MOQ, pack size, lead time, cost |
| Tên thay thế | `GET /aliases` | Ingredient aliases |
| Ngân sách & lịch | `GET /settings`, `GET /calendar-features` | Store settings và calendar |
| Lịch sử | `GET /sales-history`, `/usage-history`, `/purchase-history` | Các bảng lịch sử theo khoảng ngày |

Mọi endpoint lịch sử phải hỗ trợ ít nhất:

```text
date_from
date_to
page
page_size
```

Ví dụ:

```http
GET /api/v1/stores/STORE_001/sales-history?date_from=2026-07-01&date_to=2026-07-28&page=1&page_size=100
```

---

## 10. Database contract đề xuất

| Bảng | Source of truth cho | Ghi bởi |
|---|---|---|
| `stores` | Cửa hàng, timezone, currency | Admin/setup |
| `store_settings` | Horizon, timezone policy và cấu hình chung | Settings API/import |
| `budget_periods` | Monthly, reserved, spent theo tháng | Settings/PO confirm/PO receive |
| `ingredients` | Danh mục nguyên liệu | Ingredient API/import |
| `ingredient_aliases` | Tên khác trong file | Alias API/import |
| `products` | Danh mục món/sản phẩm | Product API/import |
| `recipe_versions` | Phiên bản recipe | Recipe API/import |
| `recipe_lines` | Thành phần và định lượng | Recipe API/import |
| `inventory_lots` | Các lô đang tồn | Import, nhận hàng |
| `inventory_movements` | Mọi biến động tồn kho | Count, adjustment, receive, usage |
| `sales_daily` | Doanh số theo ngày/sản phẩm | Import/POS |
| `usage_daily` | Tiêu thụ theo ngày/nguyên liệu | Import/reconstruction |
| `purchase_receipts` | Lịch sử backfill và receipt từ PO, có cờ side effect | Purchase-history batch/PO receive |
| `suppliers` | Nhà cung cấp | Supplier API/import |
| `supplier_ingredient_terms` | Cost, MOQ, pack, lead time | Constraint API/import |
| `calendar_features` | Ngày lễ, promotion, bối cảnh | Calendar API/import |
| `import_jobs` | Trạng thái import | Import API |
| `import_files` | File metadata/checksum | Import API |
| `import_sheet_profiles` | Sheet profile | Import API |
| `import_mappings` | Mapping đã gợi ý/xác nhận | Import API |
| `import_issues` | Warning/error theo dòng | Import API |
| `model_registry` | Model version | Training pipeline |
| `calibrators` | Calibration version | Training pipeline |
| `forecast_runs` | Metadata forecast job | Forecast API |
| `forecast_points` | P25/P50/P75 theo ngày | Forecast engine |
| `plan_runs` | Snapshot kế hoạch | Planning engine |
| `recommendations` | Đề xuất nhập theo plan | Planning engine |
| `purchase_orders` | Header PO | PO API |
| `purchase_order_lines` | Dòng PO | PO API |
| `idempotency_records` | Request hash và response của write đã xử lý | Public write middleware |
| `audit_logs` | Ai thay gì, khi nào | Mọi write API |

### 10.1. Quy tắc source of truth

- Tồn kho hiện tại được suy ra từ lô và movement; không lấy từ React state.
- Recipe theo ngày phải lấy đúng version có hiệu lực tại ngày đó.
- Forecast result không bị sửa sau khi hoàn tất; tạo run mới khi dữ liệu đổi.
- Plan giữ snapshot của forecast, tồn kho và supplier constraint đã dùng.
- PO đã `ordered` không được sửa line trực tiếp.
- Budget khả dụng luôn bằng `monthly - reserved - spent`; frontend không tự
  tính và ghi ngược vào database.
- Sales batch không tự tạo movement kho.
- Purchase-history batch `record_only` không tạo lot, inbound hoặc budget
  movement.
- Chỉ PO receive tạo inventory lot từ hàng inbound.
- Import phải lưu `import_id` hoặc `source_row_hash` trên record nguồn để chống
  ghi trùng.

---

## 11. Mapping với codebase hiện tại

### 11.1. Những endpoint frontend hiện đã gọi

`lib/shelfcash-client.ts` trong bản Frontend API Contract v1 đã có adapter cho:

```text
GET  /health
GET  /api/v1/llm/health
POST /api/v1/llm/map-sheet
POST /api/v1/imports
GET  /api/v1/imports/{import_id}
POST /api/v1/imports/{import_id}/confirm
POST /api/v1/imports/{import_id}/process
GET  /api/v1/imports/{import_id}/result
GET  /api/v1/stores/{store_id}/bootstrap
GET  /api/v1/stores/{store_id}/dashboard
GET  /api/v1/stores/{store_id}/inventory
GET  /api/v1/stores/{store_id}/products
PUT  /api/v1/stores/{store_id}/products/{product_id}/recipe
GET  /api/v1/stores/{store_id}/supplier-constraints
POST /api/v1/stores/{store_id}/supplier-constraints
PUT  /api/v1/stores/{store_id}/supplier-constraints/{constraint_id}
PUT  /api/v1/stores/{store_id}/aliases
PUT  /api/v1/stores/{store_id}/settings
PUT  /api/v1/stores/{store_id}/calendar-features
POST /api/v1/stores/{store_id}/forecast-runs
GET  /api/v1/stores/{store_id}/forecast-runs/{forecast_run_id}
GET  /api/v1/stores/{store_id}/forecast-runs/{forecast_run_id}/result
POST /api/v1/stores/{store_id}/plan-runs
GET  /api/v1/stores/{store_id}/plan-runs/{plan_run_id}
GET  /api/v1/stores/{store_id}/plan-runs/{plan_run_id}/result
POST /api/v1/stores/{store_id}/purchase-orders
GET  /api/v1/stores/{store_id}/purchase-orders
POST /api/v1/stores/{store_id}/purchase-orders/{po_id}/confirm
GET  /api/v1/stores/{store_id}/{sales|usage|purchase}-history
```

### 11.2. Phần frontend/types cần bổ sung theo audit

Repository cần thêm adapter và request/response types cho:

```text
POST  /inventory-adjustments
POST  /sales-history/batch
POST  /purchase-history/batch
PATCH /purchase-orders/{po_id}
POST  /purchase-orders/{po_id}/receive
```

`PUT /calendar-features` và `PUT /aliases` đã có adapter; body phải tiếp tục
dùng đúng wrapper `{"items": [...]}` và `{"aliases": [...]}`.

`StoreBootstrapResponse.settings` cần bổ sung:

```text
reserved_budget
spent_budget
```

PO type cần khóa:

```text
draft | ordered | partially_received | received
```

và mọi PO line response phải có `po_line_id`.

Proxy phải allowlist đúng method/path mới và chuyển tiếp `Idempotency-Key`. Sau
mọi write thành công, frontend gọi lại resource liên quan hoặc `/bootstrap`;
không giả định state local là source of truth.

---

## 12. Trình tự triển khai MVP đề xuất

### Checkpoint API-1: Persistence cơ bản

- Tạo schema database.
- `GET /stores/{store_id}/bootstrap`.
- Inventory, product, recipe, alias, settings và calendar API.
- Import `/process` ghi database theo transaction.
- Reload trang vẫn thấy dữ liệu.

### Checkpoint API-2: Forecast và planning

- Forecast run/status/result.
- Plan run/status/result.
- Lưu model version và input snapshot.
- Frontend không còn gửi toàn bộ `BootstrapData` vào `/api/plan`.

### Checkpoint API-3: Purchase Order

- Tạo Draft PO từ plan.
- Sửa Draft PO có version control.
- Confirm PO, reserve budget và tạo inbound.
- Receive một phần/toàn bộ, tạo lot và chuyển reserved sang spent.
- Danh sách/chi tiết PO.

### Checkpoint API-4: Audit và tích hợp

- Import history.
- Inventory movements.
- Recipe history.
- POS batch integration.
- Purchase-history backfill `record_only`.
- Idempotency record và audit log cho mọi public write quan trọng.
- PO receiving.
- User login và quyền theo store.

---

## 13. Luồng end-to-end chuẩn

```text
1. Frontend mở ứng dụng
2. GET bootstrap
3. Người dùng upload Excel/CSV
4. POST imports
5. Frontend hiển thị mapping
6. POST imports/{id}/confirm
7. POST imports/{id}/process
8. Backend ghi dữ liệu chuẩn vào database
9. GET imports/{id}/result
10. GET bootstrap để đồng bộ lại giao diện
11. POST forecast-runs
12. Poll forecast status và lấy result
13. POST plan-runs
14. Poll plan status và lấy recommendations
15. Người dùng chỉnh số lượng nếu cần
16. POST purchase-orders
17. Người dùng xác nhận
18. POST purchase-orders/{id}/confirm
19. Backend reserve budget và cập nhật inbound
20. GET bootstrap/dashboard để hiển thị trạng thái mới
21. Khi hàng đến, POST purchase-orders/{id}/receive
22. Backend tạo lot, tăng on_hand, giảm inbound và chuyển reserved sang spent
23. GET bootstrap/dashboard để đồng bộ tồn kho, PO và ngân sách
```

---

## 14. Tiêu chí nghiệm thu contract

Contract được xem là implement đúng khi:

- Frontend chỉ gọi Node proxy; API key không xuất hiện trong browser.
- Tải lại trang không mất dữ liệu đã lưu.
- Gọi `/process` hai lần không tạo row trùng.
- Import lỗi giữa chừng không ghi dữ liệu một nửa.
- Mỗi record nghiệp vụ thuộc đúng `store_id`.
- Recipe cũ vẫn truy xuất được sau khi tạo version mới.
- P25 luôn nhỏ hơn hoặc bằng P50, P50 nhỏ hơn hoặc bằng P75.
- Plan backend tự đọc inventory và constraint từ database.
- Override PO sai MOQ/pack size bị từ chối.
- Tổng tiền và budget do backend tính.
- PO không thể xác nhận hai lần.
- Chỉ PO `draft` được PATCH và version cũ bị từ chối.
- Sales batch không tự trừ `on_hand`.
- Purchase-history batch `record_only` không tạo lot hoặc tác động ngân sách.
- Receive một phần không được vượt quantity đã đặt.
- Confirm reserve ngân sách; receive chuyển reserved sang spent mà không trừ
  remaining budget lần hai.
- Cùng idempotency key/body không tạo side effect mới; cùng key/body khác trả
  `409 DUPLICATE_REQUEST`.
- Alias và calendar bulk upsert không xóa item ngoài request, không phụ thuộc
  thứ tự và rollback toàn bộ nếu một item lỗi.
- Error luôn có `code`, `message`, `details`.
- Mọi write quan trọng có audit hoặc metadata nguồn.

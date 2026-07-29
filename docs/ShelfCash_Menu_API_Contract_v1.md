# ShelfCash Menu API Contract v1 — Addendum

**Phiên bản:** `1.0-menu`  
**Ngày:** `2026-07-29`  
**Trạng thái:** Implementation-ready cho MVP  
**Áp dụng cùng:** `ShelfCash API Contract v1.1-consolidated`  
**Base path backend:** `/api/v1`  
**Đường dẫn frontend qua proxy:** `/api/shelfcash/api/v1`

---

## 1. Mục tiêu

Contract này bổ sung loại dữ liệu `menu` để ShelfCash có thể:

1. Nhận diện và import file `06_Menu.xlsx`.
2. Lưu món bán lẻ và combo vào database.
3. Biết combo gồm những món nào và số lượng từng món.
4. Trả danh mục Menu cho frontend sau khi import hoặc reload.
5. Nối doanh số của combo với recipe của từng món thành phần để tính đúng nhu
   cầu nguyên liệu.
6. Không trộn hai quan hệ khác nhau:
   - `combo → sản phẩm thành phần`;
   - `sản phẩm → nguyên liệu trong recipe/BOM`.

Contract này không thay đổi base path, authentication, error envelope,
store isolation, transaction hoặc idempotency đã khóa trong contract
`1.1-consolidated`.

---

## 2. Quyết định nghiệp vụ đã khóa

### 2.1. Một Menu item cũng là một product bán được

Cả món lẻ và combo đều được lưu trong danh mục `products`.

```text
product
├── single: món bán lẻ có recipe riêng
└── combo: sản phẩm bán được, tham chiếu tới nhiều single product
```

Không tạo một bản sao product riêng chỉ để hiển thị Menu.

### 2.2. Combo không phải recipe

Ví dụ:

```text
Combo Cặp Đôi
├── 1 × Trà sữa trân châu
└── 1 × Cà phê sữa
```

Đây là quan hệ `combo → product`, không phải `product → ingredient`.

Khi bán một combo, backend xử lý:

```text
Combo đã bán
→ bung thành số lượng từng món lẻ
→ lấy recipe version có hiệu lực của từng món
→ quy đổi thành nhu cầu nguyên liệu
→ ghi usage reconstructed
```

### 2.3. Không hỗ trợ combo lồng nhau trong MVP

Một combo chỉ được chứa product loại `single`.

Không cho phép:

```text
Combo A → Combo B → Món lẻ
```

Quy tắc này giúp tránh vòng lặp, double-count và làm luồng forecast dễ kiểm
chứng.

### 2.4. Giá bán là dữ liệu đầu vào chính

- `selling_price` trong file là giá thực tế của Menu item.
- Với món lẻ: `list_price = selling_price`.
- Với combo: backend tính lại `list_price` từ giá hiện tại của các món thành
  phần.
- `discount_rate` và `savings_amount` là dữ liệu kiểm tra/hiển thị; backend
  không tin trực tiếp mà phải tính lại.
- Tiền VND dùng JSON integer, ví dụ `62000`, không dùng chuỗi `"62.000 đ"`.

### 2.5. Không hard delete product đã từng được sử dụng

MVP không cần endpoint `DELETE`.

Muốn ngừng bán, frontend/backend đổi:

```json
{
  "status": "inactive"
}
```

Lịch sử bán hàng, recipe và forecast cũ vẫn phải truy vết được.

---

## 3. Canonical schema mới

Backend bổ sung khóa `menu` vào `CANONICAL_SCHEMAS`.

```python
CANONICAL_SCHEMAS = {
    # ... giữ nguyên các schema hiện có ...
    "menu": {
        "fields": [
            "product_sku",
            "item_type",
            "product_name",
            "combo_components",
            "selling_unit",
            "list_price",
            "discount_rate",
            "selling_price",
            "savings_amount",
            "status",
        ],
        "core_fields": [
            "product_sku",
            "item_type",
            "product_name",
            "selling_price",
        ],
    },
    "unknown": {"fields": [], "core_fields": []},
}

SHEET_TYPES = tuple(CANONICAL_SCHEMAS)
```

### 3.1. Ý nghĩa field

| Field | Kiểu sau chuẩn hóa | Bắt buộc | Ý nghĩa |
|---|---|---:|---|
| `product_sku` | string | Có | Mã ổn định của món/combo trong một cửa hàng |
| `item_type` | `single \| combo` | Có | Món lẻ hay combo |
| `product_name` | string | Có | Tên hiển thị |
| `combo_components` | string khi import | Có điều kiện | Chuỗi thành phần; bắt buộc khi `item_type=combo` |
| `selling_unit` | string | Không | Ví dụ `ly`, `phần`, `chai`, `cái`, `combo` |
| `list_price` | integer VND | Không | Tổng giá lẻ; backend tính lại |
| `discount_rate` | number từ `0` đến `1` | Không | Tỷ lệ giảm; `0.09` nghĩa là 9% |
| `selling_price` | integer VND | Có | Giá Menu item thực tế đang bán |
| `savings_amount` | integer VND | Không | Tiền tiết kiệm; backend tính lại |
| `status` | `active \| inactive` | Không | Mặc định `active` |

### 3.2. Field bắt buộc có điều kiện

```text
item_type = single
→ combo_components phải rỗng, null hoặc ký hiệu "—"

item_type = combo
→ combo_components bắt buộc có ít nhất một thành phần hợp lệ
```

`core_fields` chỉ thể hiện yêu cầu ở cấp mapping. Yêu cầu có điều kiện được
kiểm tra theo từng dòng ở bước process.

### 3.3. Đơn vị sản phẩm

Đơn vị sản phẩm không dùng chung enum với đơn vị nguyên liệu.

MVP nên chuẩn hóa:

```text
ly | phần | chai | cái | combo
```

Backend có thể nhận alias không dấu hoặc tiếng Anh, nhưng response phải trả giá
trị chuẩn.

---

## 4. Mapping chính xác cho `06_Menu.xlsx`

File hiện tại có 10 dòng dữ liệu: 5 món lẻ và 5 combo.

| Header trong Excel | Canonical field |
|---|---|
| `Mã món` | `product_sku` |
| `Loại` | `item_type` |
| `Tên món / Combo` | `product_name` |
| `Thành phần combo` | `combo_components` |
| `ĐVT` | `selling_unit` |
| `Tổng giá lẻ` | `list_price` |
| `Mức giảm` | `discount_rate` |
| `Giá bán` | `selling_price` |
| `Tiết kiệm` | `savings_amount` |
| `Trạng thái` | `status` |

Backend/Qwen nên nhận diện sheet này thành:

```json
{
  "sheet_type": "menu",
  "column_mapping": {
    "Mã món": "product_sku",
    "Loại": "item_type",
    "Tên món / Combo": "product_name",
    "Thành phần combo": "combo_components",
    "ĐVT": "selling_unit",
    "Tổng giá lẻ": "list_price",
    "Mức giảm": "discount_rate",
    "Giá bán": "selling_price",
    "Tiết kiệm": "savings_amount",
    "Trạng thái": "status"
  }
}
```

### 4.1. Alias cho sheet type

Backend nên chuẩn hóa các giá trị sau thành `menu`:

```text
menu
product_catalog
catalog
menu_items
products_menu
```

### 4.2. Chuẩn hóa giá trị tiếng Việt

| Giá trị trong file | Giá trị chuẩn |
|---|---|
| `Món lẻ`, `Mon le`, `single`, `retail` | `single` |
| `Combo`, `bundle` | `combo` |
| `Đang bán`, `active`, `enabled` | `active` |
| `Ngừng bán`, `inactive`, `disabled` | `inactive` |

Không phân biệt chữ hoa/thường và khoảng trắng thừa.

---

## 5. Gate xác nhận mapping

Đối với sheet `menu`, backend chỉ cho confirm khi đồng thời thỏa tất cả điều
kiện:

1. `sheet_type` là `menu`, không phải `unknown`.
2. Mỗi header nguồn có đúng một target field.
3. Không header nào map tới `null`, chuỗi rỗng, `ignore` hoặc
   `__unmapped__`.
4. Không có hai header nguồn cùng map tới một target field.
5. Có đủ bốn `core_fields`.
6. Target field thuộc đúng danh sách field của schema `menu`.

Nếu vi phạm, trả:

```http
422 Unprocessable Entity
```

```json
{
  "code": "MAPPING_INCOMPLETE",
  "message": "Mapping Menu chưa hoàn chỉnh.",
  "details": {
    "profile_id": "profile-menu-01",
    "sheet_name": "06_Menu",
    "unresolved_columns": ["Thành phần combo"],
    "missing_core_fields": [],
    "duplicate_target_fields": []
  },
  "request_id": "req_01K..."
}
```

Qwen chỉ gợi ý mapping. Qwen không được tự confirm và không được ghi trực tiếp
vào bảng nghiệp vụ.

---

## 6. Import flow

Các endpoint import giữ nguyên:

```text
POST /api/v1/imports
GET  /api/v1/imports/{import_id}
POST /api/v1/imports/{import_id}/confirm
POST /api/v1/imports/{import_id}/process
GET  /api/v1/imports/{import_id}/result
```

### 6.1. Upload

```http
POST /api/v1/imports
Content-Type: multipart/form-data
Idempotency-Key: <uuid>
```

Multipart:

```text
files=06_Menu.xlsx
store_id=STORE_001
forecast_horizon=7
```

Upload chỉ profile file và đề xuất mapping; chưa ghi product/combo vào database.

### 6.2. Response upload `201`

```json
{
  "import_id": "70d4389e-7af3-464b-a5cb-c9b21bd5ee48",
  "status": "mapping_required",
  "profiles": [
    {
      "profile_id": "profile-menu-01",
      "file_name": "06_Menu.xlsx",
      "sheet_name": "06_Menu",
      "row_count": 10,
      "columns": [
        "Mã món",
        "Loại",
        "Tên món / Combo",
        "Thành phần combo",
        "ĐVT",
        "Tổng giá lẻ",
        "Mức giảm",
        "Giá bán",
        "Tiết kiệm",
        "Trạng thái"
      ],
      "sample_rows": [
        {
          "Mã món": "MON-001",
          "Loại": "Món lẻ",
          "Tên món / Combo": "Sinh tố chuối",
          "Thành phần combo": "—",
          "ĐVT": "ly",
          "Tổng giá lẻ": 35000,
          "Mức giảm": 0,
          "Giá bán": 35000,
          "Tiết kiệm": 0,
          "Trạng thái": "Đang bán"
        }
      ]
    }
  ],
  "suggested_mappings": [
    {
      "profile_id": "profile-menu-01",
      "sheet_name": "06_Menu",
      "sheet_type": "menu",
      "column_mapping": {
        "Mã món": "product_sku",
        "Loại": "item_type",
        "Tên món / Combo": "product_name",
        "Thành phần combo": "combo_components",
        "ĐVT": "selling_unit",
        "Tổng giá lẻ": "list_price",
        "Mức giảm": "discount_rate",
        "Giá bán": "selling_price",
        "Tiết kiệm": "savings_amount",
        "Trạng thái": "status"
      },
      "confidence": 0.97,
      "source": "rule"
    }
  ],
  "warnings": [],
  "errors": [],
  "requires_review": true
}
```

### 6.3. Confirm mapping

```http
POST /api/v1/imports/{import_id}/confirm
Content-Type: application/json
```

```json
{
  "mappings": [
    {
      "profile_id": "profile-menu-01",
      "sheet_name": "06_Menu",
      "sheet_type": "menu",
      "column_mapping": {
        "Mã món": "product_sku",
        "Loại": "item_type",
        "Tên món / Combo": "product_name",
        "Thành phần combo": "combo_components",
        "ĐVT": "selling_unit",
        "Tổng giá lẻ": "list_price",
        "Mức giảm": "discount_rate",
        "Giá bán": "selling_price",
        "Tiết kiệm": "savings_amount",
        "Trạng thái": "status"
      }
    }
  ]
}
```

Response:

```json
{
  "import_id": "70d4389e-7af3-464b-a5cb-c9b21bd5ee48",
  "status": "confirmed",
  "requires_review": false,
  "warnings": [],
  "errors": []
}
```

### 6.4. Process

```http
POST /api/v1/imports/{import_id}/process
```

Backend phải thực hiện trong một transaction:

1. Kiểm tra import đang ở trạng thái `confirmed`.
2. Chuẩn hóa mọi field và validate từng dòng.
3. Upsert toàn bộ product loại `single` trước, không phụ thuộc thứ tự dòng.
4. Resolve thành phần của combo tới product loại `single` trong cùng store.
5. Upsert product loại `combo`.
6. Replace các `product_bundle_lines` của combo tương ứng.
7. Tính lại `list_price`, `discount_rate`, `savings_amount`.
8. Lưu `import_id` và `source_row_hash`.
9. Commit toàn bộ hoặc rollback toàn bộ.

Không được để trạng thái:

```text
5 món lẻ đã lưu nhưng 5 combo thất bại
```

### 6.5. Upsert key

Khóa nghiệp vụ:

```text
(store_id, product_sku)
```

Quy tắc:

- Cùng store và cùng SKU: cập nhật product hiện có.
- Khác store: là resource khác hoàn toàn.
- Cùng tên chuẩn hóa nhưng khác SKU trong cùng store: trả xung đột để người
  dùng xác nhận, không tự nhập nhằng.
- Không cho đổi `item_type` của SKU đã tồn tại; muốn chuyển loại phải tạo SKU
  mới.
- Process lại cùng `import_id` không tạo product hoặc bundle line trùng.

### 6.6. Kết quả import

`GET /api/v1/imports/{import_id}/result` bổ sung collection `menu`.

```json
{
  "store_id": "STORE_001",
  "forecast_date": "2026-07-29",
  "forecast_horizon": 7,
  "inventory": [],
  "sales_history": [],
  "usage_history": [],
  "recipes": [],
  "purchase_history": [],
  "supplier_constraints": [],
  "calendar_features": [],
  "business_constraints": [],
  "menu": [
    {
      "product_id": "106ef881-1b84-4403-8082-c38ef2fbb9ea",
      "sku": "MON-001",
      "product": "Sinh tố chuối",
      "item_type": "single",
      "selling_unit": "ly",
      "list_price": 35000,
      "price": 35000,
      "discount_rate": 0,
      "savings_amount": 0,
      "status": "active",
      "currency": "VND",
      "components": [],
      "version": 1
    }
  ],
  "validation_summary": {
    "total_rows": 10,
    "accepted_rows": 10,
    "rejected_rows": 0,
    "warning_rows": 0
  },
  "ingestion_metadata": {
    "import_id": "70d4389e-7af3-464b-a5cb-c9b21bd5ee48",
    "status": "completed",
    "committed_at": "2026-07-29T19:30:00+07:00"
  }
}
```

### 6.7. Công thức Excel

Nếu ô giá là công thức, tầng đọc workbook phải lấy được giá trị số đã tính.

Backend không được thực thi macro hoặc công thức tùy ý từ file upload.

Nếu không có cached numeric value, trả lỗi theo dòng:

```json
{
  "code": "FORMULA_VALUE_UNAVAILABLE",
  "message": "Không đọc được giá trị đã tính của ô Giá bán.",
  "details": {
    "sheet_name": "06_Menu",
    "row_number": 9,
    "column": "Giá bán"
  },
  "request_id": "req_01K..."
}
```

---

## 7. Menu item response schema

API trả Menu item theo schema sau:

```json
{
  "product_id": "8cbecb0e-f9cf-47d4-a453-45b36340a724",
  "sku": "CMB-001",
  "product": "Combo Cặp Đôi",
  "item_type": "combo",
  "selling_unit": "combo",
  "list_price": 68000,
  "price": 62000,
  "discount_rate": 0.088235,
  "savings_amount": 6000,
  "status": "active",
  "currency": "VND",
  "components": [
    {
      "component_product_id": "b910e596-b4cf-4982-886d-9e96950a867c",
      "sku": "MON-002",
      "product": "Trà sữa trân châu",
      "quantity": 1,
      "selling_unit": "ly",
      "unit_price": 39000,
      "line_list_price": 39000
    },
    {
      "component_product_id": "2e7589f5-a2c6-42c5-9ce3-a499e229e980",
      "sku": "MON-003",
      "product": "Cà phê sữa",
      "quantity": 1,
      "selling_unit": "ly",
      "unit_price": 29000,
      "line_list_price": 29000
    }
  ],
  "version": 2,
  "created_at": "2026-07-29T19:30:00+07:00",
  "updated_at": "2026-07-29T19:30:00+07:00"
}
```

Tên API `product`, `sku`, `price` được giữ để tương thích Product contract hiện
tại:

```text
product_name  --import--> product
product_sku   --import--> sku
selling_price --import--> price
```

---

## 8. Endpoint Menu

### 8.1. Danh sách Menu

```http
GET /api/v1/stores/{store_id}/menu
```

Query hỗ trợ:

```text
status=active|inactive|all
item_type=single|combo|all
search=<name-or-sku>
page=1
page_size=50
```

Mặc định:

```text
status=active
item_type=all
page=1
page_size=50
```

Response `200`:

```json
{
  "items": [
    {
      "product_id": "8cbecb0e-f9cf-47d4-a453-45b36340a724",
      "sku": "CMB-001",
      "product": "Combo Cặp Đôi",
      "item_type": "combo",
      "selling_unit": "combo",
      "list_price": 68000,
      "price": 62000,
      "discount_rate": 0.088235,
      "savings_amount": 6000,
      "status": "active",
      "currency": "VND",
      "components": [
        {
          "component_product_id": "b910e596-b4cf-4982-886d-9e96950a867c",
          "sku": "MON-002",
          "product": "Trà sữa trân châu",
          "quantity": 1,
          "selling_unit": "ly",
          "unit_price": 39000,
          "line_list_price": 39000
        },
        {
          "component_product_id": "2e7589f5-a2c6-42c5-9ce3-a499e229e980",
          "sku": "MON-003",
          "product": "Cà phê sữa",
          "quantity": 1,
          "selling_unit": "ly",
          "unit_price": 29000,
          "line_list_price": 29000
        }
      ],
      "version": 2,
      "created_at": "2026-07-29T19:30:00+07:00",
      "updated_at": "2026-07-29T19:30:00+07:00"
    }
  ],
  "summary": {
    "single_count": 5,
    "combo_count": 5,
    "active_count": 10,
    "inactive_count": 0
  },
  "page": 1,
  "page_size": 50,
  "total": 10
}
```

### Backend logic

- Mọi query bắt đầu bằng `store_id`.
- `list_price` của combo bằng tổng
  `component.quantity × component.current_price`.
- `price` của combo không tự thay đổi khi giá món thành phần thay đổi.
- `discount_rate` và `savings_amount` được tính từ giá hiện tại tại thời điểm
  response.

---

## 9. Mở rộng Product endpoints hiện có

Không tạo thêm một bộ CRUD Menu trùng với Product CRUD.

Các endpoint hiện có được mở rộng:

```text
GET   /api/v1/stores/{store_id}/products
POST  /api/v1/stores/{store_id}/products
PATCH /api/v1/stores/{store_id}/products/{product_id}
```

### 9.1. Tạo món lẻ

```http
POST /api/v1/stores/STORE_001/products
Content-Type: application/json
Idempotency-Key: <uuid>
```

```json
{
  "sku": "MON-006",
  "product": "Cacao sữa",
  "item_type": "single",
  "selling_unit": "ly",
  "price": 36000,
  "status": "active"
}
```

Response `201` trả Menu item schema với `components: []`.

### 9.2. Tạo combo

```http
POST /api/v1/stores/STORE_001/products
Content-Type: application/json
Idempotency-Key: <uuid>
```

```json
{
  "sku": "CMB-006",
  "product": "Combo Mới",
  "item_type": "combo",
  "selling_unit": "combo",
  "price": 65000,
  "status": "active",
  "components": [
    {
      "component_product_id": "106ef881-1b84-4403-8082-c38ef2fbb9ea",
      "quantity": 1
    },
    {
      "component_product_id": "2e7589f5-a2c6-42c5-9ce3-a499e229e980",
      "quantity": 1
    }
  ]
}
```

Tạo combo và component lines phải nằm trong cùng transaction.

### 9.3. Sửa tên, giá hoặc trạng thái

```http
PATCH /api/v1/stores/STORE_001/products/{product_id}
Content-Type: application/json
```

```json
{
  "version": 2,
  "product": "Combo Cặp Đôi Mới",
  "price": 63000,
  "status": "active"
}
```

Backend:

- Kiểm tra product thuộc store.
- Dùng optimistic concurrency qua `version`.
- Không cho sửa `item_type`.
- Không sửa components trong endpoint PATCH này.
- Nếu chuyển một món lẻ sang `inactive` trong khi combo `active` đang dùng món
  đó, trả `409 PRODUCT_IN_ACTIVE_COMBO`; người dùng phải tắt các combo liên
  quan trước.
- Tính lại các derived fields và tăng `version`.

Response `200` trả Menu item mới.

---

## 10. Endpoint thay thành phần combo

Đây là operation mới.

```http
PUT /api/v1/stores/{store_id}/products/{product_id}/components
Content-Type: application/json
Idempotency-Key: <uuid>
```

Request:

```json
{
  "version": 2,
  "components": [
    {
      "component_product_id": "106ef881-1b84-4403-8082-c38ef2fbb9ea",
      "quantity": 1
    },
    {
      "component_product_id": "b910e596-b4cf-4982-886d-9e96950a867c",
      "quantity": 2
    }
  ]
}
```

Backend logic:

1. Product đích phải thuộc store và có `item_type=combo`.
2. Mọi component phải thuộc cùng store.
3. Mọi component phải có `item_type=single`.
4. `quantity` là integer lớn hơn `0`.
5. Không có component trùng.
6. Replace toàn bộ bundle lines trong một transaction.
7. Tính lại derived fields.
8. Tăng `version`.

Response `200` trả Menu item đã cập nhật.

Nếu `version` cũ:

```http
409 Conflict
```

```json
{
  "code": "VERSION_CONFLICT",
  "message": "Menu item đã được cập nhật bởi request khác.",
  "details": {
    "expected_version": 2,
    "current_version": 3
  },
  "request_id": "req_01K..."
}
```

---

## 11. Bootstrap và đồng bộ frontend

`GET /api/v1/stores/{store_id}/bootstrap` bổ sung:

```json
{
  "today": "2026-07-29",
  "store": {
    "store_id": "STORE_001",
    "store_name": "Cửa hàng Quận 3",
    "timezone": "Asia/Ho_Chi_Minh",
    "currency": "VND"
  },
  "inventory": [],
  "products": [],
  "recipes": [],
  "menu": [],
  "supplier_constraints": [],
  "aliases": [],
  "future_calendar": [],
  "settings": {
    "monthly_budget": 5000000,
    "reserved_budget": 0,
    "spent_budget": 0,
    "remaining_budget": 5000000,
    "forecast_horizon": 7
  },
  "latest_runs": {
    "forecast_run_id": null,
    "plan_run_id": null
  },
  "data_freshness": {
    "inventory_updated_at": null,
    "sales_max_date": null,
    "recipes_updated_at": null,
    "menu_updated_at": "2026-07-29T19:30:00+07:00"
  }
}
```

Quy tắc:

- `menu` là read model được tạo từ `products + product_bundle_lines`, không
  phải một bảng dữ liệu trùng lặp.
- `products` vẫn được giữ để tương thích màn hình Công thức.
- Màn hình Công thức chỉ cho sửa recipe trực tiếp của product
  `item_type=single`.
- Màn hình Menu đọc collection `menu`.
- Sau import Menu thành công, frontend gọi lại `/bootstrap` hoặc `GET /menu`.

---

## 12. Parser cho thành phần combo trong file Excel

MVP chấp nhận chuỗi:

```text
1 × Trà sữa trân châu + 1 × Cà phê sữa
2 × Cà phê sữa + 1 × Trà sữa trân châu
```

Có thể nhận các ký hiệu nhân:

```text
× | x | X | *
```

Quy tắc parser:

1. Tách các thành phần bằng dấu `+`.
2. Mỗi thành phần có dạng `<positive integer> <multiply sign> <product name>`.
3. Trim khoảng trắng.
4. Resolve `product name` trong cùng store sau bước chuẩn hóa tên.
5. Chỉ resolve tới product `single`.
6. Không tự tạo product mới từ một tên thành phần không tìm thấy.
7. Không resolve cross-store.
8. Không được để combo chứa chính nó.

Ví dụ normalized:

```json
[
  {
    "component_product": "Trà sữa trân châu",
    "quantity": 1
  },
  {
    "component_product": "Cà phê sữa",
    "quantity": 1
  }
]
```

Nếu thành phần không tồn tại:

```json
{
  "code": "COMBO_COMPONENT_NOT_FOUND",
  "message": "Không tìm thấy món thành phần của combo.",
  "details": {
    "file_name": "06_Menu.xlsx",
    "sheet_name": "06_Menu",
    "row_number": 9,
    "combo_sku": "CMB-001",
    "component_name": "Trà sữa trân châu"
  },
  "request_id": "req_01K..."
}
```

---

## 13. Validation

### 13.1. Product

- `product_sku`: trim, không rỗng, duy nhất trong store, tối đa 64 ký tự.
- `product_name`: trim, không rỗng, tối đa 255 ký tự.
- `item_type`: chỉ `single` hoặc `combo`.
- `selling_price`: integer lớn hơn `0`.
- `status`: chỉ `active` hoặc `inactive`.
- `selling_unit`: thuộc enum sản phẩm được hỗ trợ.

### 13.2. Combo

- Có ít nhất một component.
- Tối đa 20 component trong MVP.
- Mỗi quantity là integer lớn hơn `0`.
- Component tồn tại và thuộc cùng store.
- Component phải là `single`.
- Combo không chứa chính nó.
- Không có component trùng.
- Combo `active` không được tham chiếu component `inactive`.

### 13.3. Derived price

Backend tính:

```text
list_price
= sum(component.quantity × component.price)

savings_amount
= max(list_price - combo.price, 0)

discount_rate
= savings_amount / list_price
```

Các giá trị này được backend dựng lại ở mỗi lần đọc/cập nhật, không cộng dồn
qua nhiều request.

Với món lẻ:

```text
list_price = price
savings_amount = 0
discount_rate = 0
```

Nếu các giá trị derived trong file lệch với kết quả backend:

- `selling_price` vẫn là source of truth.
- Backend ghi giá trị derived đã tính lại.
- Trả warning theo dòng.
- Không reject toàn import nếu các field chính vẫn hợp lệ.

Tolerance cho `discount_rate`:

```text
absolute_difference <= 0.005
```

Điều này cho phép file hiển thị 9% trong khi tỷ lệ thực từ giá làm tròn là
8.8235%.

---

## 14. Tích hợp Sales, Recipe và Forecast

### 14.1. Bán món lẻ

```text
1 × MON-001
→ recipe có hiệu lực của MON-001
→ usage nguyên liệu
```

### 14.2. Bán combo

Ví dụ bán `2 × CMB-004`, trong đó:

```text
CMB-004
├── 2 × Cà phê sữa
└── 1 × Trà sữa trân châu
```

Backend phải bung thành:

```text
4 × Cà phê sữa
2 × Trà sữa trân châu
```

Sau đó áp recipe version có hiệu lực theo ngày bán của từng món lẻ.

### 14.3. Không double-count

Một dòng POS bán combo là một SKU combo.

Frontend/file POS không cần gửi lại các món thành phần như các dòng bán độc
lập. Backend tự dựng component demand.

### 14.4. Thiếu recipe

Nếu combo hợp lệ nhưng một món thành phần thiếu recipe:

- Vẫn lưu sales history của combo.
- Dựng usage cho những component có recipe.
- Trả warning nêu rõ component thiếu recipe.
- Không tự bịa định lượng nguyên liệu.

### 14.5. Recipe endpoint

Nếu gọi:

```http
PUT /api/v1/stores/{store_id}/products/{combo_product_id}/recipe
```

với product loại `combo`, backend trả:

```http
409 Conflict
```

```json
{
  "code": "RECIPE_NOT_ALLOWED_FOR_COMBO",
  "message": "Combo sử dụng components; không lưu recipe trực tiếp.",
  "details": {
    "product_id": "8cbecb0e-f9cf-47d4-a453-45b36340a724"
  },
  "request_id": "req_01K..."
}
```

---

## 15. Database contract

### 15.1. Mở rộng bảng `products`

Các field tối thiểu:

| Column | Ý nghĩa |
|---|---|
| `product_id` | UUID primary key |
| `store_id` | FK và khóa store isolation |
| `sku` | Mã product trong store |
| `name` | Tên product |
| `item_type` | `single \| combo` |
| `selling_unit` | Đơn vị bán |
| `selling_price` | Giá bán hiện tại |
| `status` | `active \| inactive` |
| `version` | Optimistic concurrency |
| `source_import_id` | Import gần nhất tạo/cập nhật |
| `source_row_hash` | Dedupe theo dòng nguồn |
| `created_at` | Thời điểm tạo |
| `updated_at` | Thời điểm cập nhật |

Unique constraint:

```text
UNIQUE(store_id, sku)
```

### 15.2. Bảng `product_bundle_lines`

| Column | Ý nghĩa |
|---|---|
| `bundle_line_id` | UUID primary key |
| `store_id` | Khóa store isolation rõ ràng |
| `combo_product_id` | FK tới product loại `combo` |
| `component_product_id` | FK tới product loại `single` |
| `quantity` | Số món thành phần |
| `position` | Thứ tự hiển thị |
| `created_at` | Thời điểm tạo |
| `updated_at` | Thời điểm cập nhật |

Unique constraint:

```text
UNIQUE(combo_product_id, component_product_id)
```

### 15.3. Source of truth

| Dữ liệu | Source of truth |
|---|---|
| Tên, SKU, giá bán, trạng thái | `products` |
| Thành phần combo | `product_bundle_lines` |
| Tổng giá lẻ/tiết kiệm/tỷ lệ giảm hiện tại | Backend tính từ hai bảng trên |
| Recipe của món lẻ | `recipe_versions`, `recipe_lines` |
| Giá thực tế lúc bán | `sales_daily.unit_price` |
| Dữ liệu import/audit | `import_*`, `source_import_id`, `source_row_hash` |

Không lưu JSON thành phần combo như source of truth duy nhất.

---

## 16. Side effects

| Operation | Ghi products | Ghi bundle lines | Ghi sales/usage | Ghi inventory | Ghi budget/PO |
|---|---:|---:|---:|---:|---:|
| Upload Menu | Không | Không | Không | Không | Không |
| Confirm mapping | Không | Không | Không | Không | Không |
| Process Menu | Có | Có | Không | Không | Không |
| POST product | Có | Có nếu combo | Không | Không | Không |
| PATCH product | Có | Không | Không | Không | Không |
| PUT components | Có, tăng version | Có | Không | Không | Không |
| GET menu | Không | Không | Không | Không | Không |

Import Menu tuyệt đối không:

- cộng/trừ tồn kho;
- tạo purchase order;
- reserve/spend ngân sách;
- tự ghi sales history;
- tự chạy forecast;
- ghi đè recipe.

---

## 17. Error codes bổ sung

Tất cả lỗi tiếp tục dùng envelope:

```json
{
  "code": "ERROR_CODE",
  "message": "Mô tả dễ hiểu.",
  "details": {},
  "request_id": "req_01K..."
}
```

Các code mới:

```text
MENU_SCHEMA_NOT_SUPPORTED
MAPPING_INCOMPLETE
CORE_FIELDS_MISSING
DUPLICATE_TARGET_FIELD
INVALID_MENU_ITEM_TYPE
INVALID_MENU_STATUS
INVALID_PRODUCT_UNIT
INVALID_PRICE
DUPLICATE_PRODUCT_SKU
DUPLICATE_PRODUCT_NAME
PRODUCT_TYPE_IMMUTABLE
PRODUCT_IN_ACTIVE_COMBO
COMBO_COMPONENTS_REQUIRED
COMBO_COMPONENT_PARSE_ERROR
COMBO_COMPONENT_NOT_FOUND
COMBO_COMPONENT_DUPLICATE
COMBO_SELF_REFERENCE
COMBO_NESTING_NOT_SUPPORTED
INACTIVE_COMBO_COMPONENT
RECIPE_NOT_ALLOWED_FOR_COMBO
FORMULA_VALUE_UNAVAILABLE
VERSION_CONFLICT
DUPLICATE_REQUEST
```

HTTP mapping:

| HTTP | Dùng khi |
|---:|---|
| `200` | Đọc/cập nhật thành công |
| `201` | Tạo product thành công |
| `400` | Sai logic nghiệp vụ hoặc parse combo |
| `403` | Resource không thuộc store/quyền hiện tại |
| `404` | Product/component/import không tồn tại |
| `409` | Trùng SKU/name, sai version, sai state/type |
| `422` | Body/mapping/field không đúng schema |
| `500` | Lỗi backend ngoài dự kiến |

---

## 18. Authentication, store isolation và idempotency

Mọi endpoint `/api/v1` được bảo vệ theo contract hiện tại:

```http
X-ShelfCash-Key: <secret>
```

Frontend browser chỉ gọi:

```text
/api/shelfcash/api/v1/...
```

Node proxy mới được giữ API key và chuyển tiếp tới backend.

Các write operation sau phải hỗ trợ `Idempotency-Key`:

```text
POST /api/v1/imports
POST /api/v1/stores/{store_id}/products
PUT  /api/v1/stores/{store_id}/products/{product_id}/components
```

Quy tắc:

- Cùng key + cùng body: trả lại response thành công trước, không tạo side
  effect mới.
- Cùng key + body khác: `409 DUPLICATE_REQUEST`.
- PATCH dùng `version` để chống lost update.
- Mọi query/lookup product và component phải có `store_id`.

---

## 19. Endpoint discovery schema — khuyến nghị P1

Để frontend và backend không tiếp tục lệch danh sách canonical fields, nên bổ
sung:

```http
GET /api/v1/import-schemas
```

Response:

```json
{
  "schemas": {
    "menu": {
      "label": "Danh mục Menu",
      "fields": [
        "product_sku",
        "item_type",
        "product_name",
        "combo_components",
        "selling_unit",
        "list_price",
        "discount_rate",
        "selling_price",
        "savings_amount",
        "status"
      ],
      "core_fields": [
        "product_sku",
        "item_type",
        "product_name",
        "selling_price"
      ]
    }
  }
}
```

P0 có thể tiếp tục hard-code cùng schema ở hai codebase. P1 nên để backend là
source of truth và frontend dựng dropdown từ endpoint này.

---

## 20. Phạm vi triển khai

### P0 — bắt buộc để import và hiển thị Menu

1. Thêm `menu` vào `CANONICAL_SCHEMAS` và `SHEET_TYPES`.
2. Thêm rule/Qwen prompt để nhận diện 10 header của `06_Menu.xlsx`.
3. Enforce gate mapping đầy đủ.
4. Migration mở rộng `products` và tạo `product_bundle_lines`.
5. Process import theo transaction, upsert single trước combo.
6. Bổ sung `menu` vào import result và bootstrap.
7. Implement `GET /stores/{store_id}/menu`.
8. Bung combo trước khi dựng usage từ sales.
9. Thêm validation, error codes và tests ở mục 21.
10. Proxy/frontend allowlist `GET /menu` nếu proxy hiện dùng allowlist.

### P1 — CRUD đầy đủ sau khi P0 ổn định

1. Mở rộng POST/PATCH Product theo mục 9.
2. Implement PUT components theo mục 10.
3. Implement `GET /import-schemas`.
4. Thêm lịch sử giá/menu version nếu cần audit dài hạn.

---

## 21. Acceptance tests

Backend chỉ được xem là hoàn thành khi tối thiểu vượt các ca sau.

### 21.1. Happy path với file hiện tại

```text
Given 06_Menu.xlsx có 5 món lẻ + 5 combo
When upload → confirm mapping → process
Then accepted_rows = 10
And products được upsert = 10
And product_bundle_lines = 11 dòng
And import status = completed
And GET /menu trả 10 item
```

Số bundle lines kỳ vọng:

```text
CMB-001: 2
CMB-002: 2
CMB-003: 2
CMB-004: 2
CMB-005: 3
Total:   11
```

### 21.2. Reload persistence

```text
Given import completed
When reload frontend và gọi GET /bootstrap
Then menu vẫn có 10 item
And 5 combo vẫn có đúng components
```

### 21.3. Mapping chưa đủ

```text
Given "Thành phần combo" = __unmapped__
When POST /imports/{id}/confirm
Then 422 MAPPING_INCOMPLETE
And process không được phép chạy
```

### 21.4. Thiếu core field

```text
Given không header nào map tới selling_price
When confirm
Then 422 MAPPING_INCOMPLETE
And details.missing_core_fields chứa selling_price
```

### 21.5. Component không tồn tại

```text
Given combo tham chiếu "Món Không Có"
When process
Then COMBO_COMPONENT_NOT_FOUND
And transaction rollback
And không product nào của import được ghi một nửa
```

### 21.6. Combo lồng nhau

```text
Given CMB-002 chứa CMB-001
When process
Then 409 COMBO_NESTING_NOT_SUPPORTED
```

### 21.7. Idempotency

```text
Given cùng import_id được process hai lần
Then product count và bundle line count không tăng
```

### 21.8. Store isolation

```text
Given STORE_001 có MON-001
When tạo combo ở STORE_002 tham chiếu product_id của STORE_001
Then 403 FORBIDDEN hoặc 404 RESOURCE_NOT_FOUND
And không tiết lộ dữ liệu cross-store
```

### 21.9. Bung combo khi bán

```text
Given CMB-004 = 2 Cà phê sữa + 1 Trà sữa trân châu
When sales quantity_sold = 2 cho CMB-004
Then reconstructed component demand =
  4 Cà phê sữa + 2 Trà sữa trân châu
And ingredient usage được tính từ recipe version đúng ngày bán
```

### 21.10. Derived price

```text
Given CMB-001 components có tổng giá 68000
And combo price = 62000
Then list_price = 68000
And savings_amount = 6000
And discount_rate ≈ 0.088235
```

### 21.11. Optimistic concurrency

```text
Given current version = 3
When PATCH/PUT components gửi version = 2
Then 409 VERSION_CONFLICT
And không ghi thay đổi
```

### 21.12. Không gây side effect ngoài contract

```text
When process Menu
Then inventory không đổi
And budget không đổi
And PO không đổi
And sales/usage không tự phát sinh
And recipe không bị ghi đè
```

---

## 22. Checklist bàn giao cho backend

- [ ] `menu` xuất hiện trong `CANONICAL_SCHEMAS`.
- [ ] Qwen/rule mapper nhận diện đúng file `06_Menu.xlsx`.
- [ ] Confirm từ chối mọi header chưa nối.
- [ ] `product_sku`, `item_type`, `product_name`, `selling_price` là core.
- [ ] Conditional validation bắt buộc components đối với combo.
- [ ] Có migration `products` và `product_bundle_lines`.
- [ ] Có unique `(store_id, sku)`.
- [ ] Process toàn bộ trong một transaction.
- [ ] Single được resolve trước combo, không phụ thuộc row order.
- [ ] Có dedupe/idempotency bằng `import_id` và `source_row_hash`.
- [ ] Không hỗ trợ nested combo trong MVP.
- [ ] `GET /stores/{store_id}/menu` trả components đã resolve.
- [ ] Import result và bootstrap có collection `menu`.
- [ ] Bán combo được bung đúng trước khi áp recipe.
- [ ] Không hard delete, dùng `status=inactive`.
- [ ] Mọi query bảo đảm store isolation.
- [ ] Error dùng đúng envelope `{code, message, details, request_id}`.
- [ ] Import Menu không tác động inventory, PO hoặc budget.
- [ ] Test file hiện tại cho kết quả `10 products + 11 bundle lines`.

---

## 23. Luồng end-to-end cuối cùng

```text
Người dùng chọn 06_Menu.xlsx
→ POST /imports
→ Qwen/rule nhận diện sheet_type=menu
→ frontend hiển thị 10 dropdown mapping
→ người dùng nối đủ mọi header
→ POST /imports/{id}/confirm
→ POST /imports/{id}/process
→ backend upsert 5 single products
→ backend resolve và upsert 5 combo
→ backend ghi 11 product_bundle_lines
→ transaction commit
→ GET /imports/{id}/result
→ GET /stores/{store_id}/bootstrap hoặc GET /menu
→ frontend hiển thị Menu thật
→ sales combo sau này được bung thành món lẻ
→ recipe/BOM quy đổi món lẻ thành nguyên liệu
→ forecast và plan dùng demand nguyên liệu đúng
```

# Tiền xử lý file Excel sau khi gọi `/imports`

Tài liệu này mô tả **luồng đang chạy trong backend** (không phải thiết kế dự kiến). Endpoint upload thực tế là `POST /api/v1/imports`; một số màn hình có thể gọi ngắn là “/import”. Quá trình gồm ba bước riêng: **upload và gợi ý mapping → xác nhận mapping → chuẩn hóa, kiểm tra và ghi dữ liệu**.

```text
Excel/CSV → POST /api/v1/imports → profile từng sheet + mapping gợi ý
          → POST /api/v1/imports/{id}/confirm → mapping đã xác nhận
          → POST /api/v1/imports/{id}/process → dữ liệu chuẩn + business tables
          → GET /api/v1/imports/{id}/result
```

## 1. Nhận file và đọc workbook

Request upload là `multipart/form-data`, gồm `files` (có thể nhiều file), `store_id`, `forecast_date` tùy chọn và `forecast_horizon` mặc định 7 ngày (1–90). Route import dùng `x-shelfcash-key` theo cấu hình API; có thể gửi `Idempotency-Key` để lặp lại đúng request mà nhận lại import cũ.

Backend thực hiện theo thứ tự:

1. Kiểm tra đuôi file `.xlsx`, `.xls`, `.xlsm`, `.csv`; giới hạn mặc định **10 file/request, 12 MB/file, 50 MB/request**. Tên file được lấy phần tên cuối để tránh lưu đường dẫn từ client. Nội dung mỗi file được tính SHA-256.
2. Kiểm tra `store_id` tồn tại và xử lý idempotency. File được ghi tạm dưới `runtime/uploads/{import_id}`.
3. Với Excel: đọc từng sheet (tối đa **30 sheet/file**). `.xlsx` và `.xlsm` được kiểm tra công thức: ô công thức thiếu giá trị cache bị từ chối với `FORMULA_VALUE_UNAVAILABLE`. Backend đọc giá trị đã cache, không tính lại công thức. Với CSV: đọc UTF-8 có/không BOM và coi cả file là một sheet.
4. Với Excel: quét tối đa **15 hàng đầu** để chọn hàng tiêu đề có điểm cao nhất theo số ô không rỗng, tỷ lệ chuỗi và độ khác nhau của tên cột. Đọc lại sheet với hàng đó làm header. Bỏ các hàng rỗng hoàn toàn, giới hạn **100.000 hàng dữ liệu/sheet**, bỏ khoảng trắng hai đầu tên cột và tạo tên `column_N` cho header rỗng.
5. Tạo **profile** cho mỗi sheet: tên file/sheet, vị trí header (đếm từ 0), số hàng/cột, danh sách cột, kiểu dữ liệu suy đoán và tối đa **8 hàng mẫu**. Toàn bộ hàng đã đọc cũng được lưu để xử lý ở bước `/process`.

Nếu một bước đọc hoặc lưu thất bại, upload trả lỗi và dọn các file tạm/đã chuyển tên của request. Upload thành công tạo import ở trạng thái `mapping_required`; **chưa ghi dữ liệu vào các bảng nghiệp vụ**.

## 2. Gợi ý mapping dữ liệu

**Mapping có hướng `tên cột nguồn → tên trường chuẩn`**. `null` nghĩa là bỏ qua cột. Mỗi sheet có một `sheet_type` thuộc: `inventory`, `sales_history`, `usage_history`, `recipes`, `purchase_history`, `supplier_constraints`, `calendar_features`, `business_constraints`, `menu`; `unknown` là sheet không xử lý.

Backend gợi ý theo thứ tự:

1. **Rule:** nhận diện `sheet_type` từ từ khóa trong tên sheet (ví dụ `ton kho`/`inventory`, `sales`/`ban hang`, `recipe`/`cong thuc`, `menu`). Tên cột được chuẩn hóa chữ thường, bỏ dấu và so với danh sách alias. Riêng `menu` dùng bảng header riêng, ví dụ `Mã món → product_sku`, `Loại → item_type`, `Tên món/combo → product_name`, `Giá bán → selling_price`.
2. **Độ tin cậy:** rule tính từ mức khớp tên sheet và tỷ lệ cột khớp. Nếu thấp hơn ngưỡng mặc định **0,82**, pipeline gọi LLM để đề xuất lại khi provider khả dụng. Nếu provider không khả dụng, giữ kết quả rule với `source=rule_fallback` và `requires_review=true`.
3. **Kiểm tra cấu trúc:** cột nguồn lạ trong mapping hoặc trường đích nằm ngoài schema là lỗi; nhiều cột cùng trỏ một trường đích và thiếu trường cốt lõi tạo cảnh báo. Gợi ý được đánh dấu cần duyệt khi có lỗi, thiếu trường cốt lõi hoặc độ tin cậy dưới ngưỡng. `source` cho biết kết quả từ `rule`, `llm` hay `rule_fallback`.

Danh sách trường chuẩn đầy đủ là `GET /api/v1/import-schemas` và registry `app/core/canonical_schemas.py`. Các trường cốt lõi hiện tại:

| `sheet_type` | Trường cốt lõi cần có trong hàng |
|---|---|
| `inventory` | `snapshot_date`, `ingredient_name`, `batch_id`, `on_hand`, `unit` |
| `sales_history` | `date`, `product_name`, `quantity_sold` |
| `usage_history` | `date`, `ingredient_name`, `quantity_used` |
| `recipes` | `product_name`, `ingredient_name`, `ingredient_quantity` |
| `purchase_history` | `ingredient_name`, `quantity_received`; thêm ít nhất một trong `received_date`, `purchase_date` |
| `supplier_constraints` | `supplier_name`, `ingredient_name`, `minimum_order_quantity`, `order_unit`, `package_size`, `package_base_unit` |
| `calendar_features` | `date` |
| `business_constraints` | `constraint_type`, `value` |
| `menu` | `product_sku`, `item_type`, `product_name`, `selling_price` |

Ví dụ một sheet tồn kho có header `Ngày kiểm kê`, `Nguyên liệu`, `Mã lô`, `Tồn kho`, `ĐVT`:

```json
{
  "sheet_type": "inventory",
  "column_mapping": {
    "Ngày kiểm kê": "snapshot_date",
    "Nguyên liệu": "ingredient_name",
    "Mã lô": "batch_id",
    "Tồn kho": "on_hand",
    "ĐVT": "unit"
  }
}
```

Tên cột nguồn phải **khớp chính xác** với `profiles[].columns` trong response upload; không gửi tên đã tự sửa trên frontend. Một cột không cần dùng nên được đặt `null`. Với `menu`, bước xác nhận yêu cầu **mọi header nguồn đều được map**, không trùng trường đích, không thiếu bốn trường cốt lõi và không có trường đích ngoài schema.

## 3. Xem và xác nhận mapping

`POST /api/v1/imports` trả `import_id`, `profiles`, `suggested_mappings`, `warnings`, `errors`, `requires_review`. `GET /api/v1/imports/{import_id}` cho phép tải lại trạng thái, profile và mapping. Client nên hiển thị hàng mẫu cùng mapping để người dùng sửa cột bị nhận nhầm trước khi xác nhận.

Gửi `POST /api/v1/imports/{import_id}/confirm` với mapping của các sheet muốn xử lý. Mỗi phần tử dùng `profile_id` hoặc `sheet_id` từ response upload. Ví dụ:

```json
{
  "mappings": [
    {
      "profile_id": "<profile_id từ response upload>",
      "sheet_type": "inventory",
      "column_mapping": {
        "Ngày kiểm kê": "snapshot_date",
        "Nguyên liệu": "ingredient_name",
        "Mã lô": "batch_id",
        "Tồn kho": "on_hand",
        "ĐVT": "unit"
      }
    },
    {
      "profile_id": "<profile_id của sheet không nhập>",
      "skip": true
    }
  ]
}
```

`skip=true` chuyển sheet thành `unknown` với mọi cột map `null`. Xác nhận thủ công đặt `confidence=1.0`, nhưng không tự biến dữ liệu sai thành hợp lệ. Trường đích không thuộc schema bị từ chối; thiếu core field ở các loại sheet thường vẫn được lưu thành cảnh báo và sẽ lộ ra ở kiểm tra từng hàng. Chỉ các sheet có `mapping.confirmed=true` mới được đưa sang bước ghi dữ liệu nghiệp vụ. Sau khi xác nhận, import ở trạng thái `confirmed`.

## 4. Chuẩn hóa và kiểm tra khi `/process`

Gọi `POST /api/v1/imports/{import_id}/process?policy=atomic` sau khi xác nhận. Với từng sheet đã đọc, pipeline dùng mapping để tạo bản ghi chuẩn. Mỗi hàng có thêm `_source_file`, `_source_sheet`, `_source_excel_row` để truy ngược lỗi về Excel.

Các phép chuẩn hóa chính:

- **Ngày**: chuyển về `YYYY-MM-DD`; chuỗi ISO hoặc dạng ngày trước tháng được hỗ trợ. Không đọc được thì thành `null`.
- **Số**: bỏ khoảng trắng, xử lý dấu `,` và `.` theo vị trí để suy ra phần thập phân/hàng nghìn; không đọc được thì thành `null`.
- **Boolean**: nhận các giá trị như `true/false`, `1/0`, `yes/no`, `có/không`; không nhận ra thì thành `null`.
- **Đơn vị**: đổi alias thông dụng như `kg → kilogram`, `g → gram`, `l → liter`, `ml → milliliter`; trường `order_unit` đi qua bộ chuẩn hóa đơn vị đóng gói riêng.
- **Trường đặc thù**: `item_type`, `status`, `selling_unit`, `recipe_version` dùng các bộ chuẩn hóa riêng cho menu và công thức.

Sau chuẩn hóa, validator kiểm tra trường cốt lõi theo loại sheet. Ngoài ra, `purchase_history` cần ngày nhận hoặc ngày mua; `recipes.recipe_version` nếu có phải là số nguyên dương; hàng `menu` loại `single` không được có `combo_components`, còn `combo` phải có thông tin thành phần. Lỗi trả kèm sheet, thứ tự hàng, số hàng Excel, trường lỗi, mã lỗi và hướng sửa. `validation_summary` ghi số hàng tổng/hợp lệ/không hợp lệ theo `sheet_id` (danh sách lỗi trong summary giới hạn 100 lỗi/sheet).

Chính sách xử lý:

| `policy` | Hành vi |
|---|---|
| `atomic` (mặc định) | Kiểm tra tất cả sheet; nếu có lỗi hàng thì không ghi business data và trả lỗi. Nếu hợp lệ, ghi trong một giao dịch. |
| `partial_success` | Loại các hàng bị validator đánh dấu lỗi rồi thử ghi các hàng còn lại; các lỗi và số hàng bị loại vẫn có trong kết quả. Lỗi ở bước ghi nghiệp vụ vẫn làm rollback giao dịch. |
| `preview_only` | Lưu kết quả chuẩn hóa và báo cáo kiểm tra để xem trước; không ghi business data, trạng thái import giữ `confirmed`. |

Khi ghi thật, `ImportBusinessPersistenceService` dispatch theo `sheet_type` vào các bảng nghiệp vụ, giải quyết tên nguyên liệu/sản phẩm trong phạm vi store, lưu thông tin nguồn (`import_id`, `profile_id`, hash hàng nguồn) và ghi thống kê kết quả. Ghi thành công chuyển trạng thái `completed`; lỗi ở bước ghi rollback và chuyển `failed`.

`GET /api/v1/imports/{import_id}/result` trả dữ liệu chuẩn theo từng loại sheet, `validation_summary`, `ingestion_metadata` và `issues` khi đã có kết quả; trước đó trả lỗi import chưa sẵn sàng. Response `/process` chỉ tóm tắt trạng thái, chính sách, kiểm tra và issues; dùng `/result` để lấy các hàng chuẩn hóa.

## 5. Điểm cần nhớ khi tích hợp

- Không coi mapping gợi ý là dữ liệu đã nhập: upload chỉ tạo hồ sơ và đề xuất.
- Khi xác nhận, lấy `profile_id`/`sheet_id` và **header gốc** trực tiếp từ response của cùng import.
- Hiển thị `warnings`, `errors`, `requires_review`, `validation_summary` và `issues` để người dùng sửa file hoặc mapping.
- Các giới hạn nêu trên là **giá trị mặc định** trong `app/config.py`, có thể đổi bằng cấu hình triển khai.

Nguồn triển khai chính: `app/api/imports.py`, `app/services/import_service.py`, `app/core/excel_reader.py`, `app/core/ingestion_pipeline.py`, `app/core/rule_mapper.py`, `app/core/normalizer.py`, `app/core/validator.py`, `app/core/canonical_schemas.py`, `app/services/business_persistence.py`.

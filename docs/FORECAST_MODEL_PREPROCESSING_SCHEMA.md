# Schema tiền xử lý dữ liệu cho Forecast Model

Tài liệu này là hợp đồng dữ liệu giữa backend và Forecast Core. Mỗi record model có grain **store – product – target date**. Pipeline hiện có dùng cùng một code path khi train và inference để tránh training-serving skew.

## 1. Input contract

Backend truyền mapping gồm sales_history (DataFrame, bắt buộc) và calendar_features (DataFrame hoặc None).

### sales_history

| Cột nguồn | Kiểu | Bắt buộc | Chính sách |
| --- | --- | --- | --- |
| date | ISO date/datetime | Có | Ép về ngày 00:00; ngày lỗi bị loại. |
| store_id | string | Không | Trim; thiếu/rỗng dùng STORE_DEFAULT. Production query theo một store. |
| product_id | string | Không | Trim; thiếu/rỗng sinh PRD_hash từ normalized product_name. |
| product_name | string | Có | Trim; rỗng/thiếu bị loại. |
| quantity_sold | finite number >= 0 | Có | Ép numeric; null/lỗi/âm bị loại. |
| is_stockout | bool/null | Nên có | Chấp nhận 1/0, true/false, yes/no, có/không; null không bị đổi thành false. |
| unit | string/null | Không | Chỉ hiển thị, không feed model. |
| selling_price, revenue | number/null | Không | Chuẩn hoá numeric, chưa feed model. |
| promotion_name | string/null | Không | Chỉ provenance, chưa feed model. |

Grain canonical là date + store_key + product_key. Dòng trùng được gộp: quantity/revenue là tổng, selling price là trung bình, stockout là OR và text lấy giá trị đầu tiên. Ghi warning DUPLICATE_DAILY_ROWS_AGGREGATED.

### calendar_features

Một dòng cho một date, áp dụng cho store của lần chạy. Cột có thể có: is_weekend, is_holiday, is_store_closed, is_promotion (bool/null), temperature (number/null), rainfall (number >= 0/null), promotion_name (string/null). Calendar trùng ngày được gộp (bool OR, thời tiết trung bình). Nếu không có calendar, vẫn forecast với warning CALENDAR_FEATURES_MISSING và calendar_available=0.

## 2. Chuỗi chuyển đổi

    sales_daily + calendar
      → adapter: canonicalize kiểu dữ liệu, key, cột
      → validator: loại dòng fatal, gộp duplicate, quality report
      → daily panel: store × product × mọi ngày liên tục
      → demand state: phân biệt missing/closed/stockout
      → historical features tại cutoff
      → (cutoff_date, target_date, horizon) rows
      → deterministic + future-calendar features
      → category encoding + schema/dtype validation
      → 34 features → LightGBM P25/P50/P75

Daily panel được tạo từ ngày bán đầu tiên tới cutoff (inference) hoặc cuối kỳ dữ liệu (train), cho mỗi cặp store-product đã xuất hiện. Cửa hàng đóng hoặc mở nhưng không có record đều có quantity_sold=NA, nhưng được phân biệt lần lượt bằng STORE_CLOSED và MISSING_OPEN_DAY_RECORD. Chỉ record thực sự gửi lên với quantity_sold=0 mới là nhu cầu quan sát bằng 0.

## 3. Target và stockout

quantity_sold là doanh số quan sát, không luôn bằng demand khi hết hàng. Với ngày mở cửa và is_stockout=true, pipeline chỉ dùng record **nhỏ hơn ngày đó**, cùng store-product, không stockout, trong lookback 84 ngày:

1. Ưu tiên median các ngày cùng thứ nếu có tối thiểu 3 mẫu.
2. Nếu không đủ, dùng median các ngày gần đây nếu có tối thiểu 3 mẫu.
3. Estimate = max(quantity_sold, median_reference); không thấp hơn số đã bán.
4. Chỉ estimate confidence high/medium mới được chấp nhận. Confidence thấp không được làm target hay lag/rolling state.

train_eligible=true khi cửa hàng mở, target có giá trị và không phải stockout chưa tái dựng. Training còn lọc history_observation_count >= 28.

## 4. Schema ngay trước model

Một row dự báo demand cho (store_key, product_key, target_date), từ trạng thái biết tại cutoff_date; horizon = target_date - cutoff_date. Khi train, target là demand_proxy tại target date. Inference không có target.

| Nhóm | Cột model | Kiểu sau cùng | Ý nghĩa |
| --- | --- | --- | --- |
| Categorical | store_code, product_code | int32 | Encoder fit chỉ train; unseen = -1. |
| Horizon/history | horizon, history_observation_count, last_observed_demand | float64 | Khoảng dự báo và lịch sử hữu dụng. |
| Lag | cutoff_lag_1, cutoff_lag_2, cutoff_lag_7, cutoff_lag_14, cutoff_lag_28 | float64 | Demand trước cutoff. |
| Rolling | rolling_mean_{7,14,28}, rolling_median_{7,14,28}, rolling_std_{7,14,28}, mean_last_7_minus_previous_7 | float64 | Mức nền, biến động, trend ngắn hạn. |
| Stockout | stockout_count_{7,28}, stockout_rate_{7,28} | float64 | Thiếu hàng lịch sử. |
| Seasonal target lag | seasonal_lag_7_target, seasonal_lag_14_target, seasonal_lag_28_target | float64 | Demand target_date-lag, chỉ join nếu reference date <= cutoff. |
| Deterministic calendar | target_day_of_week, target_is_weekend, target_month, target_day_of_month, target_week_of_month, target_week_of_year | float64 | Suy ra từ target date. |
| Known future calendar | target_is_holiday, target_store_closed, target_temperature, target_rainfall, calendar_available | float64 | Calendar tại target date; weather có thể NaN. |

Tổng cộng **34 features**: 2 categorical và 32 numeric. Tên/thứ tự cột là contract versioned trong artifact feature_schema.json. Không fill 0 cho NaN lag/rolling/weather: LightGBM xử lý missing value, còn fill 0 sẽ lẫn “chưa đủ lịch sử” với giá trị thật.

## 5. Quy trình train

1. Service đọc sales từ cutoff - history_days + 1 đến cutoff; calendar đọc đến cutoff + max_horizon.
2. Adapter/validator chuẩn hoá, loại/gộp dữ liệu và trả quality report.
3. Tạo daily panel, phân loại missing sales, tái dựng stockout, tạo historical features.
4. Với từng cutoff lịch sử và horizon 1..7, ghép target tương lai, seasonal lag an toàn theo cutoff, deterministic features và future calendar.
5. Split theo thời gian thành train/calibration/test, không random split. Fit encoder chỉ trên train; train ba quantile models; calibration CQR; đánh giá test và walk-forward.
6. Publish atomically staging artifacts: model, mapping, feature schema, preprocessing config, quality report, metrics, fingerprint và checksum.

## 6. Quy trình inference

1. API nhận cutoff/horizon, nạp active immutable model, đọc sales không muộn hơn cutoff và calendar tới target cuối.
2. Chạy đúng pipeline preprocessing như train.
3. Lấy state đúng cutoff, tạo row cho từng horizon, thêm future features.
4. Dùng encoder trong artifact, validate đủ feature/tên/dtype rồi predict P25/P50/P75, sửa quantile crossing và áp CQR.
5. Nếu target store closed, đặt toàn bộ P25/P50/P75/interval/baseline bằng 0. Persist prediction kèm version/warnings; chỉ persist residual khi actual đã có.

## 7. Bất biến vận hành

- Không feature nào được nhìn sau cutoff; seasonal lookup có guard reference_date <= cutoff_date.
- Key là store_key + product_key + date; mọi merge phải kiểm cardinality.
- Giữ quality_report, target_quality, sales_missing_reason, method/confidence reconstruction để audit/debug nhưng không feed model.
- Trả warnings có hành động: UNSEEN_PRODUCT, INSUFFICIENT_HISTORY, INSUFFICIENT_SEASONAL_HISTORY, STORE_PLANNED_CLOSED, CALIBRATION_FALLBACK_GLOBAL.
- Khác biệt schema, thứ tự feature hoặc categorical mapping so với artifact phải fail fast, không dự báo im lặng.

## 8. Lưu trữ đề xuất

Không cần bảng trung gian production: sales_daily và calendar_features là source of truth. Nếu cần debug/training offline, persist feature snapshot với key (model_version, store_id, cutoff_date, product_id, target_date, horizon) và metadata dataset_fingerprint, preprocessing_config_version, feature_schema_version, created_at. Snapshot là dữ liệu dẫn xuất, không thay thế dữ liệu nghiệp vụ gốc.

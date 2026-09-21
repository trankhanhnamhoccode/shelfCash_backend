# Strategy Selection Presentation — FE Contract

## Endpoint

```http
GET /api/v1/decision-runs/{decision_run_id}/brief
```

FE dùng trường `strategy_selection_presentation` để hiển thị lý do kế hoạch
được chọn và lý do từng strategy không được chọn. Không cần gọi endpoint
`/explanation` cho use case này.

## Contract

```ts
type Strategy = "lean" | "balanced" | "protected";
type StrategyCardStatus = "selected" | "rejected" | "not_selected";

interface StrategyPresentationNote {
  strategy: Strategy;
  label: string;
  status: StrategyCardStatus;
  status_label: string;
  headline: string;
  message: string;
  detail_lines: string[];
  reason_codes: string[];
  evidence_ids: string[];
}

interface StrategySelectionPresentation {
  source: "deterministic";
  outcome: "selected" | "no_feasible_strategy";
  selected_strategy: Strategy | null;
  headline: string;
  summary: string | null;
  strategy_notes: StrategyPresentationNote[];
}
```

`strategy_selection_presentation` là typed field trong response `/brief`.
`reason_codes` và `evidence_ids` chỉ dành cho audit/debug, không phải input để
FE tạo câu chữ.

## Ví dụ: có kế hoạch được chọn

```json
{
  "strategy_selection_presentation": {
    "source": "deterministic",
    "outcome": "selected",
    "selected_strategy": "balanced",
    "headline": "Đã chọn phương án Cân bằng.",
    "summary": "Phương án Cân bằng là lựa chọn khả thi phù hợp nhất trong các phương án đã đánh giá.",
    "strategy_notes": [
      {
        "strategy": "lean",
        "label": "Tiết kiệm",
        "status": "rejected",
        "status_label": "Bị loại",
        "headline": "Phương án Tiết kiệm",
        "message": "Bị loại vì chi phí nhập dự kiến là 1,2 triệu đồng, cao hơn ngân sách 750 nghìn đồng.",
        "detail_lines": [
          "Mức đáp ứng nhu cầu dự kiến thấp hơn mức yêu cầu."
        ],
        "reason_codes": ["BUDGET", "SERVICE_LEVEL_REQUIREMENT"],
        "evidence_ids": ["strategy-reason:..."]
      },
      {
        "strategy": "balanced",
        "label": "Cân bằng",
        "status": "selected",
        "status_label": "Được chọn",
        "headline": "Phương án Cân bằng",
        "message": "Được chọn vì là phương án khả thi có chi phí thấp nhất.",
        "detail_lines": [],
        "reason_codes": ["LOWEST_EXACT_VALID_CANDIDATE_COST"],
        "evidence_ids": ["strategy-reason:..."]
      }
    ]
  }
}
```

## Ví dụ: không có phương án khả thi

```json
{
  "strategy_selection_presentation": {
    "source": "deterministic",
    "outcome": "no_feasible_strategy",
    "selected_strategy": null,
    "headline": "Không có phương án nào đáp ứng đầy đủ các điều kiện lựa chọn hiện tại.",
    "summary": null,
    "strategy_notes": []
  }
}
```

Nếu có strategy candidates cùng hard findings, `strategy_notes` vẫn chứa từng
card với lý do riêng. Headline chung không tự suy diễn một nguyên nhân chung.

## Quy tắc render FE

FE nên:

- Render `headline` và `summary` khi khác `null`.
- Lặp `strategy_notes`, render trực tiếp `label`, `status_label`, `message` và
  `detail_lines`.
- Dùng `strategy` hoặc `status` chỉ để chọn thứ tự/style/icon.
- Chấp nhận `strategy_notes=[]` khi Decision Run thiếu projection chi tiết cũ.
- Render nguyên văn các câu giống nhau giữa hai cards nếu backend trả như vậy.
  Hai strategy có thể bị loại bởi cùng một ràng buộc thực tế.

FE không nên:

- Map `reason_codes` sang câu tiếng Việt.
- Tự đọc `critic.findings`, warning, cost hay scenario để suy diễn nguyên nhân.
- Đổi `outcome` dựa vào số lượng card hoặc evidence trống.
- Xem warning stress/conservative là strategy failure.

## Fallback và dữ liệu lịch sử

`recommended_strategy` là business truth. Nếu Decision Run cũ có strategy được
chọn nhưng thiếu reason/candidate evidence, backend vẫn trả:

```json
{
  "outcome": "selected",
  "selected_strategy": "balanced"
}
```

Khi đó câu giải thích có thể ngắn/generic và `strategy_notes`, `reason_codes`,
hoặc `evidence_ids` có thể rỗng. FE không cần làm fallback business logic.

## Liên quan các field cũ

- `strategy_selection_presentation`: nguồn copy manager-facing chính.
- `strategy_comparison`: metrics, comparison và evidence detail; chỉ dùng cho UI
  so sánh khi cần.
- `strategy_evaluations`: projection kỹ thuật/compatibility; không dùng để FE
  tự tạo copy.
- `POST /decision-runs/{id}/explanation`: giải thích tương tác theo câu hỏi,
  không phải nguồn copy strategy-card mặc định.

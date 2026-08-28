"""Deterministic manager-facing warning presentation.

Raw codes stay in DecisionRun diagnostics.  This module only decides whether
and how an already-produced code is suitable for a manager-facing surface.
"""
from __future__ import annotations

from app.decision_intelligence.contracts import PresentedWarning
from app.decision_intelligence.risk_metadata import RISK_METADATA


_TECHNICAL_CODES = {"UNWEIGHTED_DESIGN_SCENARIOS_USE_EQUAL_CANDIDATE_WEIGHTS"}
_USER_COPY = {
    "CAPACITY_NOT_EVALUATED": (
        "Chưa thể đánh giá đầy đủ sức chứa kho",
        "Hệ thống còn thiếu thông tin cần thiết để kiểm tra khả năng lưu trữ.",
    ),
    "STRESS_SHORTAGE_OBSERVED": (
        "Có nguy cơ thiếu hàng trong một số tình huống",
        "Mô phỏng cho thấy một số nguyên liệu có thể không đủ để đáp ứng nhu cầu.",
    ),
}


def present_warnings(codes: list[str]) -> list[PresentedWarning]:
    result: list[PresentedWarning] = []
    for code in sorted({str(value) for value in codes if value}):
        metadata = RISK_METADATA.get(code)
        if code in _TECHNICAL_CODES:
            result.append(PresentedWarning(
                code=code, severity="info", audience="technical",
                title="Thông tin kỹ thuật về kịch bản mô phỏng",
                message="Kết quả dùng các trọng số kịch bản bằng nhau; thông tin này dành cho việc kiểm tra kỹ thuật.",
            ))
        elif metadata is not None:
            title, message = _USER_COPY.get(code, (metadata.title, metadata.meaning))
            result.append(PresentedWarning(
                code=code, severity=metadata.severity, audience="user",
                title=title, message=message,
            ))
        else:
            # Never surface an unmapped machine token as UI copy.
            result.append(PresentedWarning(
                code=code, severity="warning", audience="technical",
                title="Cảnh báo kỹ thuật chưa được phân loại",
                message="Cần kiểm tra chẩn đoán của lần lập kế hoạch này.",
            ))
    return result

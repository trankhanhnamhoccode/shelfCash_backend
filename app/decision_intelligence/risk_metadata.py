"""Deterministic risk/limitation metadata for Decision Assistant consumers."""
from __future__ import annotations

from dataclasses import dataclass

from app.decision_intelligence.contracts import DecisionBriefFacts, RiskDetail
from app.decision_intelligence.semantic_evidence import (
    SemanticFact,
    SemanticFactClassification,
    SemanticFactScope,
)


@dataclass(frozen=True)
class RiskMetadata:
    classification: str
    category: str
    severity: str
    title: str
    meaning: str
    recommended_action: str


def _meta(classification, category, severity, title, meaning, action) -> RiskMetadata:
    return RiskMetadata(classification, category, severity, title, meaning, action)


RISK_METADATA: dict[str, RiskMetadata] = {
    "AGGREGATE_MODEL_COUNTS_UNKNOWN_EXPIRY_LOT": _meta("limitation", "expiry", "warning", "Tồn kho chưa rõ hạn dùng", "Một phần tồn kho không có đủ thông tin hạn dùng để đánh giá đầy đủ ảnh hưởng của việc hết hạn.", "Kiểm tra và bổ sung hạn dùng cho các lô tồn kho liên quan."),
    "INBOUND_EXPIRY_NOT_EVALUATED": _meta("limitation", "expiry", "warning", "Chưa đánh giá hạn dùng hàng đang về", "Ảnh hưởng hạn dùng của một số lô hàng đang về chưa được đánh giá đầy đủ.", "Kiểm tra hạn dùng dự kiến của hàng đang về trước khi duyệt kế hoạch."),
    "PLANNED_PURCHASE_SHELF_LIFE_NOT_CONFIGURED": _meta("limitation", "expiry", "warning", "Thiếu thông tin hạn dùng hàng mua mới", "Một số hàng mua mới chưa có thông tin hạn dùng để đánh giá đầy đủ rủi ro tồn kho.", "Bổ sung thời hạn sử dụng cho hàng mua mới liên quan."),
    "UNWEIGHTED_DESIGN_SCENARIOS_USE_EQUAL_CANDIDATE_WEIGHTS": _meta("limitation", "scenario", "info", "Kịch bản được xem với trọng số ngang nhau", "Các kịch bản hiện được xem với trọng số ngang nhau, nên kết quả chưa phản ánh xác suất thực tế của từng kịch bản.", "Xem kết quả cùng với giả định kịch bản trước khi duyệt."),
    "UNWEIGHTED_SERVICE_PROBABILITY_NOT_EVALUATED": _meta("limitation", "risk_evaluation", "warning", "Chưa đánh giá xác suất đáp ứng dịch vụ", "Chưa có trọng số kịch bản phù hợp để đánh giá xác suất đáp ứng mức dịch vụ.", "Bổ sung trọng số kịch bản hoặc xem chỉ số đáp ứng xác định trước khi duyệt."),
    "UNWEIGHTED_STOCKOUT_PROBABILITY_NOT_EVALUATED": _meta("limitation", "risk_evaluation", "warning", "Chưa đánh giá xác suất thiếu hàng", "Chưa có trọng số kịch bản phù hợp để đánh giá xác suất thiếu hàng.", "Bổ sung trọng số kịch bản hoặc xem kết quả mô phỏng trước khi đánh giá rủi ro."),
    "RISK_METRIC_NOT_AVAILABLE": _meta("limitation", "risk_evaluation", "warning", "Chưa có chỉ số rủi ro", "Chưa có đủ dữ liệu để tính chỉ số rủi ro thiếu hàng cho kế hoạch này.", "Xem thêm dữ liệu hoặc kết quả mô phỏng trước khi đánh giá rủi ro."),
    "CAPACITY_NOT_EVALUATED": _meta("limitation", "capacity", "warning", "Chưa đánh giá sức chứa kho", "Kế hoạch hiện chưa đánh giá đầy đủ khả năng lưu trữ của kho.", "Kiểm tra sức chứa kho trước khi duyệt đơn."),
    "SHORTAGE_COST_FALLBACK_USED": _meta("limitation", "cost_model", "warning", "Đang dùng giả định chi phí thiếu hàng", "Mô hình đã dùng giả định chi phí thay thế cho một phần hậu quả thiếu hàng.", "Rà soát giả định chi phí thiếu hàng trước khi duyệt."),
    "SHORTAGE_CONSEQUENCE_NOT_CONFIGURED": _meta("limitation", "cost_model", "warning", "Chưa cấu hình hậu quả thiếu hàng", "Một phần hậu quả chi phí của thiếu hàng chưa được cấu hình đầy đủ.", "Bổ sung giả định hậu quả thiếu hàng trước khi dùng kết quả để so sánh chi phí."),
    "STRESS_SHORTAGE_OBSERVED": _meta("risk", "shortage", "warning", "Có thiếu hàng trong kịch bản kiểm tra", "Một kịch bản kiểm tra sức chịu đựng đã ghi nhận thiếu hàng.", "Xem các nguyên liệu có thiếu hụt trong kịch bản kiểm tra trước khi duyệt."),
    "STRESS_CAPACITY_VIOLATION": _meta("risk", "capacity", "warning", "Có vượt sức chứa trong kịch bản kiểm tra", "Một kịch bản kiểm tra sức chịu đựng đã ghi nhận lượng hàng vượt sức chứa được đánh giá.", "Kiểm tra khả năng lưu trữ của các nguyên liệu liên quan trước khi duyệt."),
    "CAPACITY_CONSEQUENCE": _meta("risk", "capacity", "critical", "Phát hiện vượt sức chứa kho", "Mô phỏng đã ghi nhận lượng hàng vượt sức chứa được đánh giá.", "Điều chỉnh kế hoạch hoặc xác nhận sức chứa kho trước khi duyệt."),
    "EXACT_SIMULATION_SAFETY_FLOOR": _meta("risk", "shortage", "critical", "Không đạt ngưỡng an toàn mô phỏng", "Mô phỏng xác định không đạt một ngưỡng an toàn đã cấu hình.", "Rà soát kết quả mô phỏng và điều kiện kế hoạch trước khi duyệt."),
    "RISK_CONSTRAINT_VIOLATION": _meta("risk", "risk_evaluation", "critical", "Vượt ngưỡng rủi ro đã cấu hình", "Kết quả đánh giá rủi ro đã vượt một ngưỡng được cấu hình.", "Rà soát kết quả đánh giá rủi ro trước khi duyệt."),
    "SERVICE_LEVEL_REQUIREMENT": _meta("risk", "supply", "critical", "Không đạt yêu cầu mức đáp ứng", "Kế hoạch không đạt một yêu cầu mức đáp ứng đã cấu hình.", "Rà soát nhu cầu, tồn kho và phương án cung ứng trước khi duyệt."),
    "UNKNOWN_EXPIRY": _meta("limitation", "expiry", "critical", "Thiếu hạn dùng bắt buộc", "Kế hoạch bị chặn vì thiếu thông tin hạn dùng bắt buộc cho hàng liên quan.", "Bổ sung thông tin hạn dùng trước khi chạy lại kế hoạch."),
    "M4_SIMULATION_FAILED": _meta("limitation", "data_quality", "critical", "Mô phỏng kiểm tra không hoàn tất", "Mô phỏng kiểm tra kế hoạch không hoàn tất nên một số đánh giá không thể xác nhận.", "Kiểm tra lỗi mô phỏng và chạy lại trước khi duyệt."),
    "M4_ACCOUNTING_INVALID": _meta("limitation", "data_quality", "critical", "Kết quả mô phỏng không nhất quán", "Kiểm tra cân đối của mô phỏng không đạt nên kết quả cần được rà soát.", "Rà soát dữ liệu mô phỏng trước khi duyệt."),
    "STRESS_ACCOUNTING_INVALID": _meta("limitation", "data_quality", "critical", "Kịch bản kiểm tra không nhất quán", "Kiểm tra cân đối của kịch bản sức chịu đựng không đạt.", "Rà soát dữ liệu kịch bản kiểm tra trước khi duyệt."),
    "CANDIDATE_MODEL_MISMATCH": _meta("limitation", "data_quality", "critical", "Kế hoạch và mô phỏng chưa khớp", "Kết quả mô hình ứng viên và mô phỏng kiểm tra chưa khớp hoàn toàn.", "Rà soát kết quả trước khi duyệt kế hoạch."),
}


def _public_classification(fact: SemanticFact) -> str:
    if fact.classification is SemanticFactClassification.RISK_SIGNAL:
        return "risk"
    if fact.classification is SemanticFactClassification.LIMITATION:
        return "limitation"
    return "unknown"


def _scope(scope: SemanticFactScope) -> str:
    return scope.value.lower()


def project_risk_details(brief: DecisionBriefFacts, facts: list[SemanticFact]) -> list[RiskDetail]:
    """Project risk semantic facts without changing their authority or meaning."""
    groups: dict[tuple, list[SemanticFact]] = {}
    for fact in facts:
        if fact.classification not in {
            SemanticFactClassification.RISK_SIGNAL,
            SemanticFactClassification.LIMITATION,
            SemanticFactClassification.UNKNOWN,
        }:
            continue
        code = str(fact.values.get("code") or fact.fact_type)
        # Scenario IDs identify provenance, not a separate UI risk card.
        entity = tuple(sorted(
            (key, value) for key, value in fact.entities.items()
            if key in {"ingredient_id", "supplier_id", "strategy"}
        ))
        key = (code, _public_classification(fact), _scope(fact.scope), entity)
        groups.setdefault(key, []).append(fact)

    ingredient_names = {
        row.ingredient_id: row.ingredient_name
        for row in [*brief.ingredient_demand, *brief.procurement_rows]
        if row.ingredient_name
    }
    details: list[RiskDetail] = []
    for (code, classification, scope, entity), grouped in groups.items():
        metadata = RISK_METADATA.get(code)
        if metadata is None or metadata.classification != classification:
            metadata = RiskMetadata(
                classification="unknown", category="unknown", severity="warning",
                title="Cảnh báo hệ thống", meaning=None, recommended_action=None,
            )
            classification = "unknown"
        entities = dict(entity)
        ingredient_id = entities.get("ingredient_id")
        evidence_ids = sorted({source for fact in grouped for source in fact.source_evidence_ids})
        details.append(RiskDetail(
            code=code,
            classification=classification,
            category=metadata.category,
            severity=metadata.severity,
            title=metadata.title,
            meaning=metadata.meaning,
            recommended_action=metadata.recommended_action,
            scope=scope,
            ingredient_id=ingredient_id,
            ingredient_name=ingredient_names.get(ingredient_id) if ingredient_id else None,
            evidence_ids=evidence_ids,
            source_count=len(grouped),
        ))
    severity_rank = {"critical": 0, "warning": 1, "info": 2}
    classification_rank = {"risk": 0, "limitation": 1, "unknown": 2}
    return sorted(
        details,
        key=lambda item: (
            classification_rank[item.classification], severity_rank[item.severity],
            item.category, item.code, item.ingredient_id or "",
        ),
    )

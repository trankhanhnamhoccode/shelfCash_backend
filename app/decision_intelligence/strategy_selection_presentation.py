"""Read-only deterministic manager presentation of persisted strategy outcomes."""
from __future__ import annotations

from app.decision_intelligence.contracts import (
    BriefStrategyEvaluation,
    StrategyPresentationNote,
    StrategySelectionPresentation,
)
from app.decision_intelligence.strategy_reason_evidence import project_strategy_reason_evidence
from app.decision_intelligence.strategy_comparison import strategy_label


_REASON_PRIORITY = {
    "BUDGET": 10,
    "SERVICE_LEVEL_REQUIREMENT": 20,
    "EXACT_SIMULATION_SAFETY_FLOOR": 20,
    "LEAD_TIME": 30,
    "MOQ": 40,
    "PACK_SIZE": 40,
    "SUPPLIER_UNAVAILABLE": 40,
    "ORDER_CUTOFF": 40,
    "SUPPLIER_MAX_QUANTITY": 40,
    "SUPPLIER_MAX_COST": 40,
    "CAPACITY_CONSEQUENCE": 40,
    "UNKNOWN_EXPIRY": 40,
    "RISK_CONSTRAINT_VIOLATION": 40,
    "EVALUATION_TECHNICAL_FAILURE": 50,
    "HIGHER_PURCHASE_COST_THAN_SELECTED": 60,
    "STRATEGY_NAME_TIEBREAK": 60,
    "LOWEST_EXACT_VALID_CANDIDATE_COST": 60,
    "SELECTION_REASON_UNAVAILABLE": 90,
}


def build_strategy_selection_presentation(package: dict, rows: list[BriefStrategyEvaluation]) -> StrategySelectionPresentation:
    """Compose cards from existing projections only; never recompute a candidate."""
    evidence = project_strategy_reason_evidence(package)
    evidence_by_strategy: dict[str, list] = {}
    for item in evidence:
        evidence_by_strategy.setdefault(item.strategy, []).append(item)
    notes = _resolve_duplicate_messages([
        _note(row, evidence_by_strategy.get(row.strategy, [])) for row in rows
    ])
    selected = next((row for row in rows if row.status == "selected"), None)
    selected_strategy = _persisted_selected_strategy(package)
    fallback = _stochastic_fallback_line(package)
    if selected:
        summary = "Phương án được chọn là lựa chọn khả thi phù hợp nhất trong các phương án đã đánh giá."
        if fallback:
            summary = f"{summary} {fallback}"
        return StrategySelectionPresentation(
            outcome="selected", selected_strategy=selected.strategy,
            headline=f"Đã chọn phương án {selected.label}.", summary=summary,
            strategy_notes=notes,
        )
    if selected_strategy:
        label = strategy_label(selected_strategy)
        summary = f"Phương án {label} là phương án được chọn trong Decision Run này."
        if fallback:
            summary = f"{summary} {fallback}"
        return StrategySelectionPresentation(
            outcome="selected", selected_strategy=selected_strategy,
            headline=f"Đã chọn phương án {label}.", summary=summary,
            strategy_notes=notes,
        )
    summary = fallback
    return StrategySelectionPresentation(
        outcome="no_feasible_strategy", selected_strategy=None,
        headline="Không có phương án nào đáp ứng đầy đủ các điều kiện lựa chọn hiện tại.",
        summary=summary, strategy_notes=notes,
    )


def _note(row: BriefStrategyEvaluation, evidence: list) -> StrategyPresentationNote:
    presentation = row.presentation
    reasons = sorted(row.reasons, key=lambda item: (_REASON_PRIORITY.get(item.code, 80), item.code))
    messages = list(presentation.reason_messages if presentation else [])
    # ``present`` emits messages in its input order.  Re-rendering is deliberately
    # avoided: preserve its existing safe wording while choosing the primary reason
    # with this local, explicit priority table.
    if reasons and presentation and row.reasons != reasons:
        from app.decision_intelligence.strategy_presentation import present
        ordered = row.model_copy(update={"reasons": reasons})
        messages = present(ordered).reason_messages
    reason_codes = [item.code for item in reasons]
    evidence_ids = sorted({item.evidence_id for item in evidence if item.reason_code in reason_codes})
    if row.status == "selected":
        message = _selected_message(row, reason_codes, messages)
        status, status_label = "selected", "Được chọn"
    elif row.status == "rejected":
        primary = messages[0] if messages else "Phương án này không đáp ứng một điều kiện bắt buộc của kế hoạch."
        message = f"Bị loại vì {_lower_first(primary)}"
        status, status_label = "rejected", "Bị loại"
    elif row.status == "feasible_not_selected":
        primary = messages[0] if messages else "Phương án này không được chọn theo kết quả đánh giá chiến lược của Decision Run."
        message = f"Không được chọn vì {_lower_first(primary)}" if messages else primary
        status, status_label = "not_selected", "Không được chọn"
    else:
        message = "Phương án này không được chọn theo kết quả đánh giá chiến lược của Decision Run."
        status, status_label = "not_selected", "Chưa được chọn"
    headline = f"Phương án {row.label}"
    return StrategyPresentationNote(
        strategy=row.strategy, label=row.label, status=status, status_label=status_label,
        headline=headline, message=message, detail_lines=messages[1:],
        reason_codes=reason_codes, evidence_ids=evidence_ids,
    )


def _selected_message(row: BriefStrategyEvaluation, codes: list[str], messages: list[str]) -> str:
    if "LOWEST_EXACT_VALID_CANDIDATE_COST" in codes:
        return "Được chọn vì là phương án khả thi có chi phí thấp nhất."
    if "STRATEGY_NAME_TIEBREAK" in codes:
        return "Được chọn theo quy tắc phân định cố định khi các phương án có cùng chi phí."
    if "SELECTION_REASON_UNAVAILABLE" in codes or row.reason_status == "unavailable":
        return "Được chọn theo kết quả đánh giá chiến lược của Decision Run; chưa có đủ bằng chứng để xác nhận lý do so sánh chi tiết."
    return messages[0] if messages else "Được chọn theo kết quả đánh giá chiến lược của Decision Run."


def _stochastic_fallback_line(package: dict) -> str | None:
    metrics = package.get("technical_metrics") if isinstance(package.get("technical_metrics"), dict) else {}
    if metrics.get("stochastic_fallback_reason") == "insufficient_effective_scenarios":
        return "Dữ liệu kịch bản ngẫu nhiên chưa đủ tin cậy, nên kế hoạch dùng các kịch bản dự báo chuẩn thay thế."
    return None


def _persisted_selected_strategy(package: dict) -> str | None:
    """Recommendation is business truth even when presentation evidence is absent."""
    value = package.get("recommended_strategy")
    return value if value in {"lean", "balanced", "protected"} else None


_DUPLICATE_SAFE_VARIANTS = {
    "BUDGET": (
        "Không khả thi vì chi phí dự kiến vượt giới hạn ngân sách hiện tại.",
        "Bị loại vì chi phí dự kiến vượt mức ngân sách cho phép.",
        "Phương án này không đáp ứng ngân sách hiện tại.",
    ),
    "SERVICE_LEVEL_REQUIREMENT": (
        "Không khả thi vì chưa đạt mức đáp ứng nhu cầu yêu cầu.",
        "Bị loại vì mức đáp ứng nhu cầu dự kiến thấp hơn mức yêu cầu.",
        "Phương án này không đáp ứng yêu cầu về mức đáp ứng nhu cầu.",
    ),
    "LEAD_TIME": (
        "Không khả thi vì hàng không thể về kịp thời điểm cần sử dụng.",
        "Bị loại vì thời gian giao hàng không đáp ứng kỳ kế hoạch.",
        "Phương án này không đáp ứng yêu cầu thời điểm nhận hàng.",
    ),
}


def _resolve_duplicate_messages(notes: list[StrategyPresentationNote]) -> list[StrategyPresentationNote]:
    """Vary only truly duplicate, code-only rejection wording deterministically."""
    by_message: dict[str, list[StrategyPresentationNote]] = {}
    for note in notes:
        by_message.setdefault(note.message, []).append(note)
    resolved = {note.strategy: note for note in notes}
    for message, duplicates in by_message.items():
        if len(duplicates) < 2:
            continue
        ordered = sorted(duplicates, key=lambda note: note.strategy)
        shared_codes = set(ordered[0].reason_codes)
        if not shared_codes or any(set(note.reason_codes) != shared_codes for note in ordered):
            continue
        primary = next((code for code in _REASON_PRIORITY if code in shared_codes), None)
        variants = _DUPLICATE_SAFE_VARIANTS.get(primary or "")
        if not variants:
            continue
        for index, note in enumerate(ordered):
            if index >= len(variants):
                break
            resolved[note.strategy] = note.model_copy(update={"message": variants[index]})
    return [resolved[note.strategy] for note in notes]


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text else text

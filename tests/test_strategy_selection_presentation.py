from app.decision_intelligence.strategy_evaluation_projection import project_strategy_evaluations
from app.decision_intelligence.strategy_presentation import present_all
from app.decision_intelligence.strategy_selection_presentation import build_strategy_selection_presentation
from app.decision_intelligence.display import purchase_cost_display


def _candidate(feasible, cost, findings=(), warnings=()):
    return {
        "is_feasible": feasible, "purchase_cost": cost,
        "critic": {"findings": list(findings), "warnings": list(warnings)},
    }


def _presentation(package):
    return build_strategy_selection_presentation(package, present_all(project_strategy_evaluations(package)))


def _selected_package():
    return {
        "recommended_strategy": "balanced",
        "strategy_selection": {
            "rule": "lowest_exact_valid_candidate_cost_then_strategy_name",
            "selected_strategy": "balanced", "eligible_candidates": ["balanced", "protected"],
        },
        "strategies": {
            "lean": _candidate(False, 1_200_000, [{"code": "SERVICE_LEVEL_REQUIREMENT", "severity": "error", "evidence": {"observed_fill_rate": .70, "required_fill_rate": .90}}]),
            "balanced": _candidate(True, 1_000_000),
            "protected": _candidate(True, 1_400_000),
        },
    }


def test_selected_and_lowest_cost_feasible_are_manager_facing_and_grounded():
    result = _presentation(_selected_package())
    assert result.outcome == "selected" and result.selected_strategy == "balanced"
    selected = next(note for note in result.strategy_notes if note.strategy == "balanced")
    assert selected.status == "selected"
    assert selected.message == "Được chọn vì là phương án khả thi có chi phí thấp nhất."
    assert selected.reason_codes == ["LOWEST_EXACT_VALID_CANDIDATE_COST"]
    assert selected.evidence_ids


def test_rejections_prioritize_exact_shortage_and_budget_amount_without_fe_copy():
    package = _selected_package()
    package["strategies"]["lean"] = _candidate(False, 1_200_000, [
        {"code": "SERVICE_LEVEL_REQUIREMENT", "severity": "error", "evidence": {"observed_fill_rate": .70, "required_fill_rate": .90}},
        {"code": "BUDGET", "severity": "error", "evidence": {"planned_cost": 1_200_000, "budget_limit": 750_000}},
    ])
    note = next(note for note in _presentation(package).strategy_notes if note.strategy == "lean")
    assert note.status == "rejected" and "Bị loại vì chi phí nhập dự kiến" in note.message
    assert "1,2 triệu đồng" in note.message and "750 nghìn đồng" in note.message
    assert note.reason_codes == ["BUDGET", "SERVICE_LEVEL_REQUIREMENT"]
    assert note.evidence_ids


def test_supplier_timing_and_each_candidate_get_its_own_safe_message():
    package = _selected_package()
    package["strategies"]["lean"] = _candidate(False, 1_100_000, [{"code": "LEAD_TIME", "severity": "error", "evidence": {"earliest_arrival_date": "2026-10-02"}}])
    package["strategies"]["protected"] = _candidate(False, 1_500_000, [{"code": "BUDGET", "severity": "error", "evidence": {}}])
    package["strategy_selection"]["eligible_candidates"] = ["balanced"]
    notes = {note.strategy: note for note in _presentation(package).strategy_notes}
    assert "không thể về kịp" in notes["lean"].message
    assert "ngân sách" in notes["protected"].message


def test_no_feasible_and_unknown_evidence_do_not_invent_a_common_cause():
    package = _selected_package()
    package["recommended_strategy"] = None
    package.pop("strategy_selection")
    package["strategies"] = {
        "lean": _candidate(False, 10, [{"code": "UNMAPPED_NEW_CONSTRAINT", "severity": "error", "evidence": {}}]),
        "balanced": _candidate(False, 20, [{"code": "LEAD_TIME", "severity": "error", "evidence": {}}]),
    }
    result = _presentation(package)
    assert result.outcome == "no_feasible_strategy"
    assert result.headline == "Không có phương án nào đáp ứng đầy đủ các điều kiện lựa chọn hiện tại."
    unknown = next(note for note in result.strategy_notes if note.strategy == "lean")
    assert "UNMAPPED_NEW_CONSTRAINT" in unknown.reason_codes
    assert "không đáp ứng một điều kiện bắt buộc" in unknown.message


def test_selected_recommendation_survives_missing_strategy_reason_evidence():
    package = {"recommended_strategy": "balanced", "strategies": {}}
    result = _presentation(package)
    assert result.outcome == "selected"
    assert result.selected_strategy == "balanced"
    assert result.strategy_notes == []
    assert "được chọn trong Decision Run" in result.summary


def test_stochastic_fallback_is_informational_not_a_rejection_or_selected_failure():
    package = _selected_package()
    package["technical_metrics"] = {"stochastic_fallback_reason": "insufficient_effective_scenarios"}
    package["strategies"]["balanced"]["critic"]["warnings"] = ["STRESS_SHORTAGE_OBSERVED", "CAPACITY_NOT_EVALUATED"]
    result = _presentation(package)
    assert "kịch bản ngẫu nhiên chưa đủ tin cậy" in result.summary
    selected = next(note for note in result.strategy_notes if note.status == "selected")
    assert selected.reason_codes == ["LOWEST_EXACT_VALID_CANDIDATE_COST"]
    assert "thiếu hàng" not in selected.message.lower()


def test_empty_old_package_degrades_without_persistence_specific_assumptions():
    result = _presentation({})
    assert result.outcome == "no_feasible_strategy"
    assert result.strategy_notes == []


def test_manager_money_formatting_preserves_thousand_and_million_ranges():
    assert purchase_cost_display(750_000) == "750 nghìn đồng"
    assert purchase_cost_display(1_200_000) == "1,2 triệu đồng"


def test_same_budget_code_uses_grounded_amounts_and_secondary_details_per_strategy():
    package = _selected_package()
    package["strategies"]["lean"] = _candidate(False, 1_120_000, [
        {"code": "BUDGET", "severity": "error", "evidence": {"planned_cost": 1_120_000, "budget_limit": 1_000_000}},
        {"code": "LEAD_TIME", "severity": "error", "evidence": {}},
    ])
    package["strategies"]["protected"] = _candidate(False, 1_680_000, [
        {"code": "BUDGET", "severity": "error", "evidence": {"planned_cost": 1_680_000, "budget_limit": 1_000_000}},
        {"code": "SERVICE_LEVEL_REQUIREMENT", "severity": "error", "evidence": {}},
    ])
    package["strategy_selection"]["eligible_candidates"] = ["balanced"]
    notes = {note.strategy: note for note in _presentation(package).strategy_notes}
    assert notes["lean"].message != notes["protected"].message
    assert "1,12 triệu" in notes["lean"].message
    assert "1,68 triệu" in notes["protected"].message
    assert any("về kịp" in line for line in notes["lean"].detail_lines)
    assert any("đáp ứng nhu cầu" in line for line in notes["protected"].detail_lines)


def test_identical_code_only_budget_rejections_use_deterministic_safe_variants():
    package = _selected_package()
    package["strategies"]["lean"] = _candidate(False, 10, [{"code": "BUDGET", "severity": "error", "evidence": {}}])
    package["strategies"]["protected"] = _candidate(False, 20, [{"code": "BUDGET", "severity": "error", "evidence": {}}])
    package["strategy_selection"]["eligible_candidates"] = ["balanced"]
    first = _presentation(package)
    second = _presentation(package)
    messages = [note.message for note in first.strategy_notes if note.status == "rejected"]
    assert len(messages) == len(set(messages))
    assert messages == [note.message for note in second.strategy_notes if note.status == "rejected"]
    assert all("ngân sách" in message for message in messages)

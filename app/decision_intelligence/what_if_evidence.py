"""Canonical deterministic facts and narration for one What-if request."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.decision_intelligence.contracts import (
    Citation,
    DecisionBriefFacts,
    DecisionExplanationResponse,
    DecisionNarrativeLLMResponse,
    ExplanationClaim,
    WhatIfComparison,
    WhatIfMutationFacts,
    WhatIfOrderChange,
    WhatIfRiskChange,
    WhatIfStrategyChange,
)
from app.llm.tasks import LLMTask


class WhatIfEvidenceFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    fact_type: str
    values: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class WhatIfFacts:
    mutation: WhatIfMutationFacts
    comparison: WhatIfComparison
    evidence: list[WhatIfEvidenceFact]


def build_what_if_facts(
    decision_run_id: str,
    baseline: DecisionBriefFacts,
    hypothetical: DecisionBriefFacts,
    body: Any,
    *,
    baseline_package: dict[str, Any],
    hypothetical_package: dict[str, Any],
) -> WhatIfFacts:
    mutation = WhatIfMutationFacts(
        demand_multiplier=float(body.demand_multiplier if body.demand_multiplier is not None else 1.0),
        demand_change_ratio=round(float(body.demand_multiplier if body.demand_multiplier is not None else 1.0) - 1.0, 12),
        demand_change_percent=round((float(body.demand_multiplier if body.demand_multiplier is not None else 1.0) - 1.0) * 100, 12),
        demand_change_percentage_points=round((float(body.demand_multiplier if body.demand_multiplier is not None else 1.0) - 1.0) * 100, 12),
        supplier_delay_days=int(body.supplier_delay_days or 0),
        budget_limit=body.budget_limit,
        strategy_override=body.strategy,
    )
    comparison = _comparison(baseline, hypothetical, mutation, baseline_package, hypothetical_package)
    scenario_key = hashlib.sha256(json.dumps(mutation.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()[:12]
    evidence = _evidence(
        decision_run_id, scenario_key, mutation, comparison, baseline, hypothetical,
        baseline_package=baseline_package, hypothetical_package=hypothetical_package,
    )
    return WhatIfFacts(mutation=mutation, comparison=comparison, evidence=evidence)


def _comparison(baseline, hypothetical, mutation, baseline_package, hypothetical_package) -> WhatIfComparison:
    def delta(left, right):
        return None if left is None or right is None else round(float(right) - float(left), 12)

    baseline_probability = _probability_metrics(baseline_package)
    hypothetical_probability = _probability_metrics(hypothetical_package)
    compatible_probability = (
        baseline_probability["compatible"]
        and hypothetical_probability["compatible"]
        and baseline_probability["metric_source"] == hypothetical_probability["metric_source"]
    )
    fill_delta = delta(
        baseline_probability["expected_fill_rate"], hypothetical_probability["expected_fill_rate"],
    ) if compatible_probability else None
    probability_delta = delta(
        baseline_probability["stockout_probability"], hypothetical_probability["stockout_probability"],
    ) if compatible_probability else None
    return WhatIfComparison(
        recommendation_changed=(
            baseline.recommendation.available != hypothetical.recommendation.available
            or baseline.recommendation.strategy != hypothetical.recommendation.strategy
        ),
        baseline_strategy=baseline.recommendation.strategy,
        hypothetical_strategy=hypothetical.recommendation.strategy,
        purchase_cost_delta=delta(
            baseline.recommendation.total_purchase_cost,
            hypothetical.recommendation.total_purchase_cost,
        ),
        expected_fill_rate_delta=fill_delta,
        expected_fill_rate_percentage_point_delta=(round(fill_delta * 100, 12) if fill_delta is not None else None),
        stockout_probability_delta=probability_delta,
        # These scalar fields have no run-level unit contract.  Keep them null
        # rather than summing kg, litres and pieces.
        shortage_quantity_delta=None,
        waste_quantity_delta=None,
        baseline_recommendation_available=baseline.recommendation.available,
        hypothetical_recommendation_available=hypothetical.recommendation.available,
        feasibility_changed=baseline.recommendation.available != hypothetical.recommendation.available,
        strategy_change=WhatIfStrategyChange(
            changed=baseline.recommendation.strategy != hypothetical.recommendation.strategy,
            baseline_strategy=baseline.recommendation.strategy,
            hypothetical_strategy=hypothetical.recommendation.strategy,
            forced_by_request=mutation.strategy_override is not None,
        ),
        order_changes=_order_changes(baseline, hypothetical),
        warnings_added=sorted(set(hypothetical.critic.warnings) - set(baseline.critic.warnings)),
        warnings_removed=sorted(set(baseline.critic.warnings) - set(hypothetical.critic.warnings)),
        hard_violations_added=sorted(set(hypothetical.critic.hard_violations) - set(baseline.critic.hard_violations)),
        hard_violations_removed=sorted(set(baseline.critic.hard_violations) - set(hypothetical.critic.hard_violations)),
        new_issues=_risk_changes(baseline, hypothetical, added=True),
        resolved_issues=_risk_changes(baseline, hypothetical, added=False),
        new_risks=_risk_changes(baseline, hypothetical, added=True),
        resolved_risks=_risk_changes(baseline, hypothetical, added=False),
    )


def _probability_metrics(package: dict[str, Any]) -> dict[str, Any]:
    metrics = package.get("business_metrics") if isinstance(package.get("business_metrics"), dict) else {}
    probabilistic = metrics.get("probabilistic") if isinstance(metrics.get("probabilistic"), dict) else {}
    source = probabilistic.get("metric_source")
    return {
        "compatible": probabilistic.get("status") == "evaluated" and bool(source),
        "metric_source": source,
        "expected_fill_rate": _number(probabilistic.get("expected_fill_rate")),
        "stockout_probability": _number(probabilistic.get("stockout_probability")),
    }


def _order_changes(baseline: DecisionBriefFacts, hypothetical: DecisionBriefFacts) -> list[WhatIfOrderChange]:
    def grouped(rows):
        result: dict[tuple[str, str | None], list] = {}
        for row in rows:
            result.setdefault((row.ingredient_id, row.unit), []).append(row)
        return result

    left, right = grouped(baseline.procurement_rows), grouped(hypothetical.procurement_rows)
    changes: list[WhatIfOrderChange] = []
    for key in sorted(set(left) | set(right)):
        baseline_rows, hypothetical_rows = left.get(key, []), right.get(key, [])
        baseline_quantity = sum(row.quantity for row in baseline_rows) if baseline_rows else None
        hypothetical_quantity = sum(row.quantity for row in hypothetical_rows) if hypothetical_rows else None
        if baseline_quantity == hypothetical_quantity:
            continue
        change_type = (
            "added" if baseline_quantity is None else "removed" if hypothetical_quantity is None
            else "increased" if hypothetical_quantity > baseline_quantity else "decreased"
        )
        all_rows = [*baseline_rows, *hypothetical_rows]
        supplier = lambda rows: rows[0].supplier_id if len({row.supplier_id for row in rows}) == 1 and rows else None
        arrival = lambda rows: rows[0].arrival_date if len({row.arrival_date for row in rows}) == 1 and rows else None
        changes.append(WhatIfOrderChange(
            ingredient_id=key[0], unit=key[1],
            ingredient_name=next((row.ingredient_name for row in all_rows if row.ingredient_name), None),
            baseline_quantity=baseline_quantity, hypothetical_quantity=hypothetical_quantity,
            quantity_delta=(None if baseline_quantity is None or hypothetical_quantity is None else round(hypothetical_quantity - baseline_quantity, 12)),
            baseline_supplier_id=supplier(baseline_rows), hypothetical_supplier_id=supplier(hypothetical_rows),
            baseline_arrival_date=arrival(baseline_rows), hypothetical_arrival_date=arrival(hypothetical_rows),
            change_type=change_type,
        ))
    return changes


def _risk_changes(baseline, hypothetical, *, added: bool) -> list[WhatIfRiskChange]:
    def keyed(brief):
        return {
            (item.code, item.classification, item.scope, item.ingredient_id): item
            for item in brief.risk_details
        }
    left, right = keyed(baseline), keyed(hypothetical)
    changes = (set(right) - set(left)) if added else (set(left) - set(right))
    return [WhatIfRiskChange(code=code, classification=classification, scope=scope, ingredient_id=ingredient_id)
            for code, classification, scope, ingredient_id in sorted(changes)]


def _evidence(
    run_id, scenario_key, mutation, comparison, baseline, hypothetical, *,
    baseline_package: dict[str, Any], hypothetical_package: dict[str, Any],
) -> list[WhatIfEvidenceFact]:
    def fact(kind, values):
        digest = hashlib.sha256(f"{run_id}|{scenario_key}|{kind}|{json.dumps(values, sort_keys=True, default=str)}".encode()).hexdigest()[:16]
        return WhatIfEvidenceFact(evidence_id=f"wi-{kind.lower().replace('_', '-')}-{digest}", fact_type=kind, values=values)

    records = [fact("WHAT_IF_MUTATION", mutation.model_dump(mode="json"))]
    records.append(fact("WHAT_IF_FEASIBILITY_CHANGE", {
        "baseline_recommendation_available": comparison.baseline_recommendation_available,
        "hypothetical_recommendation_available": comparison.hypothetical_recommendation_available,
        "feasibility_changed": comparison.feasibility_changed,
    }))
    if comparison.purchase_cost_delta is not None:
        records.append(fact("WHAT_IF_PURCHASE_COST_DELTA", {
            "baseline_purchase_cost": baseline.recommendation.total_purchase_cost,
            "hypothetical_purchase_cost": hypothetical.recommendation.total_purchase_cost,
            "purchase_cost_delta": comparison.purchase_cost_delta,
        }))
    baseline_probability = _probability_metrics(baseline_package)
    hypothetical_probability = _probability_metrics(hypothetical_package)
    if comparison.expected_fill_rate_delta is not None:
        records.append(fact("WHAT_IF_FILL_RATE_DELTA", {
            "baseline_expected_fill_rate": baseline_probability["expected_fill_rate"],
            "hypothetical_expected_fill_rate": hypothetical_probability["expected_fill_rate"],
            "expected_fill_rate_delta": comparison.expected_fill_rate_delta,
            "expected_fill_rate_percentage_point_delta": comparison.expected_fill_rate_percentage_point_delta,
        }))
    if comparison.stockout_probability_delta is not None:
        records.append(fact("WHAT_IF_STOCKOUT_PROBABILITY_DELTA", {
            "baseline_stockout_probability": baseline_probability["stockout_probability"],
            "hypothetical_stockout_probability": hypothetical_probability["stockout_probability"],
            "stockout_probability_delta": comparison.stockout_probability_delta,
        }))
    if comparison.strategy_change and comparison.strategy_change.changed:
        records.append(fact("WHAT_IF_STRATEGY_CHANGE", comparison.strategy_change.model_dump(mode="json")))
    for change in comparison.order_changes:
        records.append(fact("WHAT_IF_ORDER_CHANGE", change.model_dump(mode="json")))
    for change in comparison.new_issues:
        records.append(fact("WHAT_IF_RISK_CHANGE", {"change": "new", **change.model_dump(mode="json")}))
    for change in comparison.resolved_issues:
        records.append(fact("WHAT_IF_RISK_CHANGE", {"change": "resolved", **change.model_dump(mode="json")}))
    return records


class WhatIfNarrativeProvider:
    """One on-demand Qwen wording call over precomputed What-if facts."""

    def __init__(self, llm_provider):
        self.llm_provider = llm_provider

    def explain(self, decision_run_id: str, facts: WhatIfFacts) -> DecisionExplanationResponse:
        fallback = self.deterministic_fallback(decision_run_id, facts)
        if not self.llm_provider or not self.llm_provider.available:
            return fallback
        try:
            payload = {
                "intent": "WHAT_IF",
                "language": "vi",
                "detail_level": "simple",
                "evidence": [item.model_dump(mode="json") for item in facts.evidence],
                "instruction": (
                    "Describe only this hypothetical simulation. Do not calculate values or infer mechanisms. "
                    "Treat a strategy_override as a user-requested scenario, never as an optimizer selection. "
                    "Do not name a supplier unless an evidence fact identifies it, and never expose machine risk codes."
                ),
            }
            raw = _run_async(self.llm_provider.generate_json(
                "You narrate grounded What-if results in concise Vietnamese. "
                "Use intervention wording only when the cited evidence includes both the mutation and its computed outcome.", payload,
                task=LLMTask.DECISION_NARRATIVE,
                request_context={"decision_run_id": decision_run_id, "what_if": True},
            ))
            typed = DecisionNarrativeLLMResponse.model_validate(raw)
            return self._guard(decision_run_id, typed, facts)
        except Exception:
            return fallback

    def _guard(self, decision_run_id, raw, facts) -> DecisionExplanationResponse:
        by_id = {item.evidence_id: item for item in facts.evidence}
        used = raw.used_evidence_ids
        if not set(used) <= set(by_id):
            raise ValueError("unsupported_used_evidence_id")
        claims, cited = [], set()
        for claim in raw.claims:
            if not claim.evidence_ids or not set(claim.evidence_ids) <= set(by_id):
                raise ValueError("unsupported_evidence_id")
            items = [by_id[item_id] for item_id in claim.evidence_ids]
            if claim.type not in {item.fact_type for item in items}:
                raise ValueError("unsupported_claim_type")
            _validate_numbers(claim.text, items)
            _validate_intervention_language(claim.text, items)
            _validate_mechanism_language(claim.text)
            _validate_risk_language(claim.text, items)
            cited.update(claim.evidence_ids)
            claims.append(ExplanationClaim(type=claim.type, value=claim.text, evidence_ids=claim.evidence_ids))
        if set(used) != cited:
            raise ValueError("used_evidence_ids_mismatch")
        # `answer` is public narrative too; it may not smuggle a number or a
        # mechanism that the structured claims did not contain.
        answer_items = [by_id[item_id] for item_id in used]
        _validate_numbers(raw.answer, answer_items)
        _validate_intervention_language(raw.answer, answer_items)
        _validate_mechanism_language(raw.answer)
        _validate_risk_language(raw.answer, answer_items)
        _validate_raw_machine_code(raw.answer)
        citations = [Citation(evidence_id=item.evidence_id, label=item.fact_type, source_type="what_if")
                     for item in facts.evidence if item.evidence_id in cited]
        return DecisionExplanationResponse(
            source="openrouter_qwen", language="vi", detail_level="simple",
            summary=raw.answer, why_this_plan=[raw.answer], main_risks=[], tradeoffs=[],
            important_assumptions=["What-if values are computed before narration."],
            decision_run_id=decision_run_id, answer=raw.answer, intent="WHAT_IF",
            entities={"ingredient_ids": [], "supplier_ids": []}, claims=claims,
            citations=citations, grounded=True, provider="openrouter_qwen", raw_response=raw.model_dump(mode="json"),
        )

    def deterministic_fallback(self, decision_run_id: str, facts: WhatIfFacts) -> DecisionExplanationResponse:
        mutation, comparison = facts.mutation, facts.comparison
        parts = [_mutation_sentence(mutation)]
        if comparison.hypothetical_recommendation_available is False:
            parts.append("Trong kịch bản giả định này, ShelfCash không tìm được kế hoạch nhập khả thi.")
        else:
            if comparison.purchase_cost_delta not in (None, 0):
                parts.append(_delta_sentence("Chi phí mua", comparison.purchase_cost_delta))
            if comparison.expected_fill_rate_percentage_point_delta not in (None, 0):
                parts.append(_point_delta_sentence(comparison.expected_fill_rate_percentage_point_delta))
            if comparison.order_changes:
                parts.append(_order_sentence(comparison.order_changes[0]))
            if comparison.strategy_change and comparison.strategy_change.changed:
                if comparison.strategy_change.forced_by_request:
                    parts.append("Kịch bản sử dụng chiến lược theo lựa chọn được yêu cầu.")
                else:
                    parts.append("Kết quả giả lập có thay đổi chiến lược được đề xuất.")
            if comparison.new_issues:
                if any(item.classification == "risk" for item in comparison.new_issues):
                    parts.append("Kịch bản giả định xuất hiện thêm tín hiệu rủi ro cần theo dõi.")
                elif any(item.classification == "limitation" for item in comparison.new_issues):
                    parts.append("Kịch bản giả định có thêm hạn chế trong đánh giá.")
            if len(parts) == 1:
                parts.append("Các chỉ số chính được theo dõi không thay đổi trong kết quả mô phỏng này.")
        answer = " ".join(parts)
        return DecisionExplanationResponse(
            source="deterministic_fallback", language="vi", detail_level="simple",
            summary=answer, why_this_plan=[answer], main_risks=[], tradeoffs=[],
            important_assumptions=["What-if is a hypothetical simulation."],
            decision_run_id=decision_run_id, answer=answer, intent="WHAT_IF",
            entities={"ingredient_ids": [], "supplier_ids": []}, claims=[], citations=[],
            grounded=True, provider="deterministic_fallback",
        )


def _run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(coro)).result()


def _validate_numbers(text: str, items: list[WhatIfEvidenceFact]):
    raw_values = [round(float(value), 9) for item in items for value in item.values.values() if isinstance(value, (int, float))]
    # Vietnamese prose commonly expresses a negative delta through a preceding
    # word such as "giảm" rather than a minus sign.  The direction itself is
    # still supplied by the deterministic fact; accept its display magnitude.
    supported = set(raw_values) | {abs(value) for value in raw_values}
    pattern = r"(?<![\w])[-+]?\d{1,3}(?:[.,]\d{3})+(?:[.,]\d+)?|(?<![\w])[-+]?\d+(?:[.,]\d+)?"
    for value in re.findall(pattern, text):
        if round(_parse_display_number(value), 9) not in supported:
            raise ValueError("unsupported_numeric_claim")


def _parse_display_number(value: str) -> float:
    compact = value.replace(" ", "")
    if compact.count(".") > 1 or compact.count(",") > 1:
        return float(compact.replace(".", "").replace(",", ""))
    if "." in compact and "," in compact:
        decimal = "." if compact.rfind(".") > compact.rfind(",") else ","
        thousands = "," if decimal == "." else "."
        return float(compact.replace(thousands, "").replace(decimal, "."))
    separator = "." if "." in compact else "," if "," in compact else None
    if separator and len(compact.rsplit(separator, 1)[1]) == 3:
        return float(compact.replace(separator, ""))
    return float(compact.replace(",", "."))


def _validate_intervention_language(text: str, items: list[WhatIfEvidenceFact]):
    lowered = f" {text.lower()} "
    causal = (" khi ", " vì ", " do ", " nên ", " because ", " due to ")
    if not any(marker in lowered for marker in causal):
        return
    types = {item.fact_type for item in items}
    outcome = {"WHAT_IF_PURCHASE_COST_DELTA", "WHAT_IF_FILL_RATE_DELTA", "WHAT_IF_STOCKOUT_PROBABILITY_DELTA", "WHAT_IF_ORDER_CHANGE", "WHAT_IF_FEASIBILITY_CHANGE", "WHAT_IF_STRATEGY_CHANGE", "WHAT_IF_RISK_CHANGE"}
    if "WHAT_IF_MUTATION" not in types or not types & outcome:
        raise ValueError("unsupported_intervention_claim")


def _validate_mechanism_language(text: str):
    lowered = text.lower()
    if any(token in lowered for token in ("pack size", "quy cách đóng gói", "moq", "lead time", "ngân sách bị chạm")):
        raise ValueError("unsupported_mechanism_claim")


def _validate_raw_machine_code(text: str):
    if re.search(r"\b[A-Z][A-Z0-9]+(?:_[A-Z0-9]+)+\b", text):
        raise ValueError("raw_machine_code_in_narrative")


def _validate_risk_language(text: str, items: list[WhatIfEvidenceFact]):
    if "rủi ro tăng" not in text.lower():
        return
    if any(item.fact_type == "WHAT_IF_RISK_CHANGE" and item.values.get("classification") != "risk" for item in items):
        raise ValueError("limitation_is_not_risk")


def _mutation_sentence(mutation: WhatIfMutationFacts) -> str:
    parts = ["Trong kịch bản giả định"]
    if mutation.demand_change_percent > 0:
        parts.append(f"nhu cầu tăng {_format(mutation.demand_change_percent)}%")
    elif mutation.demand_change_percent < 0:
        parts.append(f"nhu cầu giảm {_format(abs(mutation.demand_change_percent))}%")
    if mutation.supplier_delay_days:
        parts.append(f"thời gian giao của nhà cung cấp tăng đồng loạt thêm {mutation.supplier_delay_days} ngày")
    if mutation.budget_limit is not None:
        parts.append(f"giới hạn ngân sách là {_format(mutation.budget_limit)}")
    return ", ".join(parts) + "."


def _delta_sentence(label: str, delta: float) -> str:
    direction = "tăng" if delta > 0 else "giảm"
    return f"{label} {direction} {_format(abs(delta))} trong kết quả mô phỏng."


def _point_delta_sentence(delta: float) -> str:
    direction = "tăng" if delta > 0 else "giảm"
    return f"Mức đáp ứng {direction} {_format(abs(delta))} điểm phần trăm."


def _order_sentence(change: WhatIfOrderChange) -> str:
    name = change.ingredient_name or "một nguyên liệu"
    if change.change_type == "added":
        return f"Kế hoạch giả định bổ sung lượng mua cho {name}."
    if change.change_type == "removed":
        return f"Kế hoạch giả định không còn lượng mua cho {name}."
    direction = "tăng" if change.change_type == "increased" else "giảm"
    return f"Lượng mua {name} {direction} {_format(abs(change.quantity_delta or 0))} {change.unit or ''}.".strip()


def _number(value: object) -> float | None:
    return None if value is None else float(value)


def _format(value: float | int) -> str:
    return f"{float(value):,.6f}".rstrip("0").rstrip(".").replace(",", "X").replace(".", ",").replace("X", ".")

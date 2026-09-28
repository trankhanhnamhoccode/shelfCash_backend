"""Small deterministic scope gate for Decision Explanation questions."""

from enum import StrEnum
import re

from app.core.names import normalize_lookup_name
from app.services.decision.budget_what_if import is_budget_what_if_question


class QuestionScope(StrEnum):
    BUDGET_WHAT_IF = "budget_what_if"
    BUDGET = "budget"
    ENTITY_OPERATIONAL = "entity_operational"
    PLAN_STRATEGY = "plan_strategy"
    GENERAL_DECISION = "general_decision"
    CLOSED_FACT = "closed_fact"
    UNSUPPORTED = "unsupported"


_OUT_OF_DOMAIN = ("muon an", "want to eat", "i want to eat")
_ENTITY_OPERATIONAL_MARKERS = (
    "mua", "nhap", "dat", "can", "nhu cau", "demand", "thieu", "stockout",
    "buy", "buying", "order", "ordering", "need",
)
_PLAN_STRATEGY_MARKERS = (
    "phuong an", "chien luoc", "strategy", "trade off", "trade-off", "tradeoff", "chon",
    "selected", "de xuat", "lean", "balanced", "protected",
)
_GENERAL_DECISION_MARKERS = ("ke hoach", "plan", "rui ro", "risk", "chu y", "van de", "problem", "gia dinh", "assumption")
_WHY_MARKERS = ("tai sao", "vi sao", "why")
_CLOSED_FACT_MARKERS = (
    "tong chi phi", "total cost", "ke hoach nao duoc chon", "phuong an nao duoc chon",
    "which plan is selected", "vuot ngan sach", "over budget",
)
_BUDGET_MARKERS = (
    "ngan sach", "budget", "con du", "con bao nhieu tien", "con bao nhieu ngan sach",
    "vuot bao nhieu", "lo ngan sach", "lo budget", "dung bao nhieu ngan sach",
    "dung het ngan sach",
)


def classify_question_scope(question: str | None, *, has_explicit_ingredient_id: bool) -> QuestionScope:
    """Classify the Decision Run object being asked about, not an answer template.

    This deterministic boundary decides scope before generic ``why`` wording.
    Canonical ingredient identity is still resolved later by the existing
    resolver. Closed facts retain the existing narrative flow until a later
    approved execution policy exists.
    """
    if not question or not question.strip():
        return QuestionScope.ENTITY_OPERATIONAL if has_explicit_ingredient_id else QuestionScope.GENERAL_DECISION
    normalized = normalize_lookup_name(question)
    if _contains_any(normalized, _OUT_OF_DOMAIN):
        return QuestionScope.UNSUPPORTED
    if has_explicit_ingredient_id:
        return QuestionScope.ENTITY_OPERATIONAL
    if is_budget_what_if_question(question):
        return QuestionScope.BUDGET_WHAT_IF
    # Budget is a single bounded evidence family.  It must win over the older
    # CLOSED_FACT marker so budget questions never fall through to generic
    # narration without the Decision Run budget snapshot.
    if _contains_any(normalized, _BUDGET_MARKERS):
        return QuestionScope.BUDGET
    if _contains_any(normalized, _CLOSED_FACT_MARKERS):
        return QuestionScope.CLOSED_FACT
    if _contains_any(normalized, _PLAN_STRATEGY_MARKERS):
        return QuestionScope.PLAN_STRATEGY
    if _contains_any(normalized, _GENERAL_DECISION_MARKERS):
        return QuestionScope.GENERAL_DECISION
    if _contains_any(normalized, _ENTITY_OPERATIONAL_MARKERS):
        return QuestionScope.ENTITY_OPERATIONAL
    if _contains_any(normalized, _WHY_MARKERS):
        return QuestionScope.GENERAL_DECISION
    return QuestionScope.UNSUPPORTED


def _contains_any(text: str, phrases: tuple[str, ...]) -> bool:
    return any(re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", text) for phrase in phrases)

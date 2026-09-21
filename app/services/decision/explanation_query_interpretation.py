"""Small deterministic intent gate for Explanation question routing."""

from enum import StrEnum
import re

from app.core.names import normalize_lookup_name


class ExplanationQueryIntent(StrEnum):
    GENERIC = "generic"
    INGREDIENT = "ingredient"
    UNSUPPORTED = "unsupported"


_OUT_OF_DOMAIN = ("muon an", "want to eat", "i want to eat")
_INGREDIENT_MARKERS = (
    "mua", "nhap", "dat", "can", "tai sao", "vi sao", "why", "explain",
    "giai thich", "van de", "problem", "nhu cau", "demand",
)
_GENERIC_MARKERS = (
    "ke hoach", "plan", "rui ro", "risk", "trade off", "tradeoff",
    "gia dinh", "assumption", "chien luoc", "strategy",
)


def classify_explanation_question(question: str | None, *, has_explicit_ingredient_id: bool) -> ExplanationQueryIntent:
    """Classify explanation purpose without resolving canonical identity.

    An ingredient selector is not itself proof that arbitrary user prose asks
    for a procurement explanation. Explicit IDs retain their established
    target behavior except for recognized out-of-domain requests.
    """
    if not question or not question.strip():
        return ExplanationQueryIntent.INGREDIENT if has_explicit_ingredient_id else ExplanationQueryIntent.GENERIC
    normalized = normalize_lookup_name(question)
    if _contains_any(normalized, _OUT_OF_DOMAIN):
        return ExplanationQueryIntent.UNSUPPORTED
    if has_explicit_ingredient_id:
        return ExplanationQueryIntent.INGREDIENT
    if _contains_any(normalized, _INGREDIENT_MARKERS):
        return ExplanationQueryIntent.INGREDIENT
    if _contains_any(normalized, _GENERIC_MARKERS):
        return ExplanationQueryIntent.GENERIC
    return ExplanationQueryIntent.UNSUPPORTED


def _contains_any(text: str, phrases: tuple[str, ...]) -> bool:
    return any(re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", text) for phrase in phrases)

"""High-confidence state assertions checked against selected narrative evidence."""
from __future__ import annotations

import re
import unicodedata


def _plain(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.casefold()).replace("đ", "d")
    return "".join(char for char in decomposed if unicodedata.category(char) != "Mn")


def validate_semantic_state(text: str, items: list[dict]) -> None:
    """Reject explicit budget state claims that contradict the persisted snapshot.

    Only recognizable assertions are checked; this is not a general language
    inference engine. Numeric amounts remain the numeric registry's concern.
    """
    budget = next((item for item in items if item.get("type") == "BUDGET_STATUS"), None)
    if budget is None:
        return
    wording = _plain(text)
    availability = budget.get("availability")
    says_missing = bool(re.search(
        r"(?:khong|chua) (?:co|du) (?:du )?(?:du lieu|thong tin) ngan sach|"
        r"(?:budget|budget data) (?:is )?(?:unavailable|missing)|"
        r"no (?:budget|budget data|budget information)", wording,
    ))
    if says_missing and availability == "available":
        raise ValueError("budget_availability_contradicted")
    if availability != "available":
        # A definite exceeded/not-exceeded claim requires the complete snapshot.
        if _budget_exceeded_assertion(wording) is not None:
            raise ValueError("budget_state_unavailable")
        return
    assertion = _budget_exceeded_assertion(wording)
    if assertion is not None and assertion is not budget.get("exceeds_budget"):
        raise ValueError("budget_exceeded_state_contradicted")


def _budget_exceeded_assertion(wording: str) -> bool | None:
    if not any(word in wording for word in ("ngan sach", "budget")):
        return None
    if re.search(r"(?:khong|chua) (?:vuot|lo) (?:qua )?(?:muc )?(?:ngan sach|budget)|"
                 r"(?:within|under) (?:the )?budget|nam trong (?:muc )?ngan sach|"
                 r"con du (?:ngan sach|budget)", wording):
        return False
    if re.search(r"(?:vuot|lo) (?:qua )?(?:muc )?(?:ngan sach|budget)|"
                 r"exceed(?:s|ed|ing)? (?:the )?budget|over budget", wording):
        return True
    return None

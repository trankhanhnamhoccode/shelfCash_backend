"""Small deterministic parser for standalone budget What-if questions."""
from __future__ import annotations

from dataclasses import dataclass
import re

from app.core.names import normalize_lookup_name


@dataclass(frozen=True)
class BudgetMutation:
    budget_limit: int | None
    issue: str | None = None


def is_budget_what_if_question(question: str | None) -> bool:
    if not question or not question.strip():
        return False
    text = normalize_lookup_name(question)
    has_budget = any(marker in text for marker in ("ngan sach", "budget"))
    relative = any(marker in text for marker in ("tang them", "giam", "tang ngan sach", "giam ngan sach"))
    absolute = any(marker in text for marker in ("budget la", "ngan sach la", "chi co"))
    return (has_budget and (relative or absolute or "neu" in text)) or ("neu" in text and (relative or "chi co" in text))


def resolve_budget_mutation(question: str | None, *, current_budget: int | float | None) -> BudgetMutation:
    text = normalize_lookup_name(question)
    amount = _money_amount(text)
    if amount is None:
        return BudgetMutation(None, "amount_required")
    if amount < 0:
        return BudgetMutation(None, "invalid_amount")
    is_relative = any(marker in text for marker in ("tang them", "giam ngan sach", "giam budget", "tang ngan sach", "tang budget"))
    if is_relative:
        if current_budget is None:
            return BudgetMutation(None, "current_budget_unavailable")
        result = int(current_budget) + amount if "giam" not in text else int(current_budget) - amount
        return BudgetMutation(result if result >= 0 else None, None if result >= 0 else "invalid_amount")
    return BudgetMutation(amount)


def current_budget_snapshot(package: dict) -> int | None:
    technical = package.get("technical_metrics") if isinstance(package, dict) else None
    diagnostics = technical.get("scenario_diagnostics") if isinstance(technical, dict) else None
    snapshot = diagnostics.get("budget_snapshot") if isinstance(diagnostics, dict) else None
    value = snapshot.get("budget_limit") if isinstance(snapshot, dict) else None
    return int(value) if isinstance(value, (int, float)) and value >= 0 else None


def _money_amount(text: str) -> int | None:
    match = re.search(r"(?<![a-z0-9])(\d+(?:[.,]\d+)?)\s*(nghin|ngan|k|trieu|tr)\b", text)
    if match:
        value = float(match.group(1).replace(",", "."))
        unit = match.group(2)
        return int(round(value * (1_000_000 if unit in {"trieu", "tr"} else 1_000)))
    match = re.search(r"(?<![a-z0-9])(\d{4,})(?![a-z0-9])", text)
    return int(match.group(1)) if match else None

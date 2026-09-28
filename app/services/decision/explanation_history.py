"""Bounded, untrusted client history used only for referent resolution."""
from __future__ import annotations

from dataclasses import dataclass

from app.core.names import normalize_lookup_name


@dataclass(frozen=True)
class HistoryResolution:
    question: str
    clarification: str | None = None


def resolve_follow_up(question: str | None, history, brief) -> HistoryResolution:
    question = question or ""
    text = normalize_lookup_name(question)
    turns = [normalize_lookup_name(turn.content) for turn in history]
    joined = " ".join(turns)
    strategies = [name for name in ("lean", "balanced", "protected") if name in joined]
    unique_strategies = list(dict.fromkeys(strategies))
    ambiguous_reference = any(marker in text for marker in ("cai kia", "cai do", "phuong an kia", "phuong an do"))
    if ambiguous_reference and len(unique_strategies) > 2:
        return HistoryResolution(question, "Bạn đang hỏi phương án nào: LEAN, BALANCED hay PROTECTED?")
    if ("cai nao" in text or "con balanced" in text or "con lean" in text) and len(unique_strategies) == 2:
        return HistoryResolution(f"{unique_strategies[0]} so với {unique_strategies[1]} {question}")
    if "tang them" in text and any(marker in joined for marker in ("ngan sach", "budget")):
        return HistoryResolution(f"Nếu tăng ngân sách {question}")
    if any(marker in text for marker in ("khong nhap", "khong mua", "without purchase")):
        names = []
        for row in [*brief.procurement_rows, *brief.ingredient_demand]:
            if row.ingredient_name and normalize_lookup_name(row.ingredient_name) in joined:
                names.append(row.ingredient_name)
        names = list(dict.fromkeys(names))
        if len(names) == 1:
            return HistoryResolution(f"{question} {names[0]}")
        if len(names) > 1:
            return HistoryResolution(question, "Bạn đang hỏi nguyên liệu nào?")
    return HistoryResolution(question)

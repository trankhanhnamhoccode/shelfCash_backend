"""Bounded, untrusted client history used only for referent resolution."""
from __future__ import annotations

from dataclasses import dataclass

from app.core.names import normalize_lookup_name
from app.services.decision.explanation_strategy_aliases import mentioned_strategies


@dataclass(frozen=True)
class HistoryResolution:
    question: str
    clarification: str | None = None


def resolve_follow_up(question: str | None, history, brief) -> HistoryResolution:
    question = question or ""
    text = normalize_lookup_name(question)
    turns = [normalize_lookup_name(turn.content) for turn in history]
    joined = " ".join(turns)
    current_names = list(dict.fromkeys(
        row.ingredient_name for row in [*brief.procurement_rows, *brief.ingredient_demand]
        if row.ingredient_name
    ))
    prior_user_turns = [
        turn.content for turn in history
        if getattr(turn, "role", "user") == "user"
    ]
    if history and getattr(history[-1], "role", None) == "assistant" and "ban muon hoi" in normalize_lookup_name(history[-1].content):
        exact = [name for name in current_names if normalize_lookup_name(name) == text]
        if len(exact) == 1 and prior_user_turns:
            return HistoryResolution(f"{exact[0]}: {prior_user_turns[-1]}")
    strategies = [name for name in ("lean", "balanced", "protected") if name in mentioned_strategies(joined)]
    unique_strategies = list(dict.fromkeys(strategies))
    ambiguous_reference = any(marker in text for marker in ("cai kia", "cai do", "phuong an kia", "phuong an do"))
    if ambiguous_reference and len(unique_strategies) > 2:
        return HistoryResolution(question, "Bạn đang hỏi phương án nào: LEAN, BALANCED hay PROTECTED?")
    if ("cai nao" in text or "con balanced" in text or "con lean" in text) and len(unique_strategies) == 2:
        return HistoryResolution(f"{unique_strategies[0]} so với {unique_strategies[1]} {question}")
    if "tang them" in text and any(marker in joined for marker in ("ngan sach", "budget")):
        return HistoryResolution(f"Nếu tăng ngân sách {question}")
    if any(marker in text for marker in ("khong nhap", "khong mua", "bo nguyen lieu", "bo khoi don", "without purchase")):
        names = []
        for prior in reversed(prior_user_turns):
            matched = [name for name in current_names if normalize_lookup_name(name) in normalize_lookup_name(prior)]
            if matched:
                names = matched
                break
        names = list(dict.fromkeys(names))
        if len(names) == 1:
            return HistoryResolution(f"{question} {names[0]}")
        if len(names) > 1:
            return HistoryResolution(question, "Bạn đang hỏi nguyên liệu nào?")
    return HistoryResolution(question)

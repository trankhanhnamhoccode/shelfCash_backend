"""Shared manager-facing display values for decision intelligence."""
from __future__ import annotations


def vi_number(value: float, maximum_decimals: int = 2) -> str:
    rendered = f"{float(value):,.{maximum_decimals}f}".rstrip("0").rstrip(".")
    return rendered.replace(",", "X").replace(".", ",").replace("X", ".")


def purchase_cost_display(value: float | int | None) -> str | None:
    if not isinstance(value, (int, float)):
        return None
    amount = float(value)
    if amount >= 1_000_000:
        return f"{vi_number(amount / 1_000_000)} triệu đồng"
    return f"{vi_number(amount, 0)} đồng"

"""Shared manager-facing display values for decision intelligence."""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from app.core.units import normalize_unit


_DISCRETE_COUNT_UNITS = {"cái"}


def _precision_for_unit(unit: str | None) -> int:
    try:
        return 0 if unit and normalize_unit(unit) in _DISCRETE_COUNT_UNITS else 2
    except ValueError:
        return 2


def _rounded_decimal(value: float | int | Decimal, precision: int) -> Decimal:
    quantum = Decimal("1") if precision == 0 else Decimal("1").scaleb(-precision)
    return Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP)


def vi_number(value: float | int | Decimal, maximum_decimals: int = 2) -> str:
    rounded = _rounded_decimal(value, maximum_decimals)
    rendered = f"{rounded:,.{maximum_decimals}f}".rstrip("0").rstrip(".")
    return rendered.replace(",", "X").replace(".", ",").replace("X", ".")


def purchase_cost_display(value: float | int | None) -> str | None:
    if not isinstance(value, (int, float)):
        return None
    amount = Decimal(str(value))
    if amount >= 1_000_000:
        return f"{vi_number(amount / Decimal('1000000'))} triệu đồng"
    return f"{vi_number(amount, 0)} đồng"


def percentage_display(value: float | int | None) -> str | None:
    return f"{vi_number(Decimal(str(value)) * Decimal('100'), 2)}%" if isinstance(value, (int, float)) else None


def date_display(value: str | None) -> str | None:
    return f"{value[8:10]}/{value[5:7]}" if isinstance(value, str) and len(value) == 10 and value[4] == "-" else value


def add_numeric_display_contract(record: dict) -> dict:
    """Attach exact, backend-owned numeric text that narrative may repeat."""
    allowed: list[str] = list(record.get("allowed_numeric_mentions") or [])
    display_values = dict(record.get("display_values") or {})

    def allow(key: str, value: object) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return
        precision = _precision_for_unit(record.get("unit"))
        rendered = vi_number(value, precision)
        english_rendered = rendered.replace(",", ".")
        display_values.setdefault(key, rendered)
        allowed.extend([rendered, english_rendered])
        if float(value) < 0:
            absolute = abs(float(value))
            allowed.extend([
                vi_number(absolute, precision), vi_number(absolute, precision).replace(",", "."),
            ])

    rate_keys = {"expected_fill_rate", "fill_rate", "stockout_probability"}
    for key, value in record.items():
        if key not in {"total_purchase_cost", "purchase_cost", *rate_keys}:
            allow(key, value)

    for key in rate_keys:
        value = record.get(key)
        if isinstance(value, (int, float)):
            rendered = f"{vi_number(Decimal(str(value)) * Decimal('100'), 2)}%"
            display_values[key] = rendered
            allowed.append(rendered)

    for key, value in list(record.items()):
        if key.endswith("_date") and isinstance(value, str) and len(value) == 10 and value[4] == "-":
            rendered = f"{value[8:10]}/{value[5:7]}"
            display_values.setdefault(key, rendered)
            allowed.extend([value, rendered])

    cost = record.get("total_purchase_cost")
    if isinstance(cost, (int, float)):
        rendered = purchase_cost_display(cost)
        if rendered:
            display_values["total_purchase_cost"] = rendered
            allowed.append(rendered)

    if record.get("type") == "DEMAND_HORIZON_SUMMARY":
        low, high, unit = record.get("daily_p50_min"), record.get("daily_p50_max"), record.get("unit")
        if isinstance(low, (int, float)) and isinstance(high, (int, float)) and unit:
            precision = _precision_for_unit(str(unit))
            rendered = f"{vi_number(low, precision)}–{vi_number(high, precision)} {unit}/ngày"
            display_values["daily_demand_range"] = rendered
            allowed.append(rendered)

    record["display_values"] = display_values
    record["allowed_numeric_mentions"] = list(dict.fromkeys(item for item in allowed if item))
    return record

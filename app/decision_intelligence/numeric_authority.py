"""Structured numeric authority for grounded Decision Explanation."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
import re
from typing import Any


class NumericKind(StrEnum):
    MONEY = "money"
    PERCENT = "percent"
    COUNT = "count"
    QUANTITY = "quantity"
    DAYS = "days"


@dataclass(frozen=True)
class AuthorizedNumericFact:
    value: Decimal
    kind: NumericKind
    semantic_key: str
    evidence_id: str
    unit: str | None = None
    provenance: str | None = None
    scenario_id: str | None = None
    entities: tuple[tuple[str, str], ...] = ()
    display_mentions: tuple[str, ...] = ()
    approximate_allowed: bool = False


_MONEY = ("cost", "budget", "spend", "over", "remaining", "purchase")
_PERCENT = ("rate", "probability", "utilization", "pct", "percent")
_QUANTITY = ("quantity", "demand", "inventory", "fulfilled", "shortage", "waste", "ending", "p25", "p50", "p75", "gap", "value")
_NUMERIC_MENTION = re.compile(r"(?<![\w-])(?:\d{1,3}(?:[.,]\d{3})+|\d+(?:[.,]\d+)?)(?:\s*(?:triệu|tr\b|nghìn|ngàn|kg|g\b|litres?|liters?|l\b|cái|sku|nguyên liệu|ingredients?|ngày|days?|vnd|đồng|k\b|%|phần trăm))?", re.IGNORECASE)


@dataclass(frozen=True)
class NumericMention:
    value: Decimal
    kind: NumericKind | None
    unit: str | None
    text: str
    start: int
    end: int


def build_numeric_authority(evidence: list[dict[str, Any]]) -> list[AuthorizedNumericFact]:
    facts: list[AuthorizedNumericFact] = []
    for item in evidence:
        evidence_id = str(item.get("evidence_id", "legacy"))
        values = dict(item)
        if isinstance(item.get("values"), dict):
            values.update(item["values"])
        unit = _normalize_unit(values.get("unit") or item.get("unit"))
        displays = tuple(str(v) for v in (item.get("allowed_numeric_mentions") or ()) if isinstance(v, str))
        provenance = str(item.get("source_path") or item.get("type") or "")
        scenario_id = _optional_string(values.get("scenario_id") or item.get("scenario_id"))
        entities = tuple(sorted(
            (str(key), str(value)) for key, value in item.items()
            if key.endswith("_strategy") or key in {"strategy", "ingredient_id", "supplier_id"}
        ))
        for key, raw in values.items():
            if isinstance(raw, bool) or not isinstance(raw, (int, float, Decimal)):
                continue
            classification = _fact_kind(key, raw, unit)
            if classification is None:
                continue
            kind, value, approx = classification
            display_values = item.get("display_values") if isinstance(item.get("display_values"), dict) else {}
            display = display_values.get(key)
            facts.append(AuthorizedNumericFact(
                value, kind, key, evidence_id, unit if kind is NumericKind.QUANTITY else None,
                provenance, scenario_id, entities,
                (str(display),) if isinstance(display, str) else (), approx,
            ))
        # Compatibility for legacy aggregate evidence whose numeric projection
        # is present only in backend-generated display mentions.
        for mention in displays:
            parsed = parse_numeric_mention(mention)
            if parsed:
                legacy_kind = parsed[1]
                if legacy_kind is None:
                    legacy_kind = (
                        NumericKind.QUANTITY
                        if str(item.get("type")) in {"DEMAND_HORIZON_SUMMARY", "DEMAND_ORDER_ALIGNMENT", "PROCUREMENT_QUANTITY", "NO_PLANNED_PURCHASE_BASELINE", "INGREDIENT_OPERATIONAL_RISK"}
                        else NumericKind.COUNT
                    )
                facts.append(AuthorizedNumericFact(
                    parsed[0], legacy_kind, "legacy_display", evidence_id,
                    (parsed[2] or unit) if legacy_kind is NumericKind.QUANTITY else None,
                    provenance, scenario_id, entities, (mention,), False,
                ))
    return facts


def iter_numeric_mentions(text: str):
    for match in _NUMERIC_MENTION.finditer(text):
        parsed = parse_numeric_mention(match.group(0))
        if parsed is not None:
            yield NumericMention(*parsed, match.group(0), match.start(), match.end())


def parse_numeric_mention(text: str) -> tuple[Decimal, NumericKind | None, str | None] | None:
    lowered = text.casefold().strip()
    token = re.search(r"\d{1,3}(?:[.,]\d{3})+|\d+(?:[.,]\d+)?", lowered)
    if not token:
        return None
    value = _parse_decimal(token.group(0))
    if value is None:
        return None
    unit = _normalize_unit(lowered)
    if any(marker in lowered for marker in ("triệu", "nghìn", "ngàn", "đồng", "vnd")) or re.search(r"\b(?:tr|k)\b", lowered):
        if "triệu" in lowered or re.search(r"\btr\b", lowered):
            value *= Decimal("1000000")
        elif "nghìn" in lowered or "ngàn" in lowered or re.search(r"\bk\b", lowered):
            value *= Decimal("1000")
        return value, NumericKind.MONEY, None
    if "%" in lowered or "phần trăm" in lowered:
        return value, NumericKind.PERCENT, None
    if unit in {"kg", "g", "l", "liter", "litre", "cái"}:
        return value, NumericKind.QUANTITY, unit
    if unit in {"ngày", "day"}:
        return value, NumericKind.DAYS, unit
    if unit in {"sku", "nguyên liệu", "ingredient"}:
        return value, NumericKind.COUNT, unit
    return value, None, None


def equivalent_or_rounded(mention: NumericMention, fact: AuthorizedNumericFact, *, approximate: bool) -> bool:
    if mention.kind is not None and mention.kind is not fact.kind:
        return False
    if mention.kind is NumericKind.QUANTITY and mention.unit and fact.unit and mention.unit != fact.unit:
        return False
    if mention.value == fact.value:
        return True
    if not approximate or not fact.approximate_allowed:
        return False
    if fact.kind is NumericKind.MONEY and fact.value:
        return abs(mention.value - fact.value) / abs(fact.value) <= Decimal("0.03")
    if fact.kind is NumericKind.PERCENT:
        return abs(mention.value - fact.value) <= Decimal("1")
    return False


def render_fact(fact: AuthorizedNumericFact) -> str:
    if fact.display_mentions:
        rendered = fact.display_mentions[0]
        if fact.kind is NumericKind.MONEY and not any(marker in rendered.casefold() for marker in ("triệu", "nghìn", "đồng", "vnd")):
            rendered = ""
        if not rendered:
            return _render_without_display(fact)
        if fact.kind is not NumericKind.QUANTITY or not fact.unit or fact.unit in rendered.casefold():
            return rendered
        return f"{rendered} {fact.unit}"
    return _render_without_display(fact)


def _render_without_display(fact: AuthorizedNumericFact) -> str:
    if fact.kind is NumericKind.MONEY:
        if abs(fact.value) >= Decimal("1000000"):
            return f"{_vi(fact.value / Decimal('1000000'))} triệu đồng"
        if abs(fact.value) >= Decimal("1000"):
            return f"{_vi(fact.value / Decimal('1000'), 0)} nghìn đồng"
        return f"{_vi(fact.value, 0)} đồng"
    if fact.kind is NumericKind.PERCENT:
        return f"{_vi(fact.value)}%"
    return f"{_vi(fact.value)}{(' ' + fact.unit) if fact.unit else ''}"


def _fact_kind(key: str, raw: int | float | Decimal, unit: str | None):
    lower = key.casefold()
    value = Decimal(str(raw))
    if any(marker in lower for marker in _MONEY):
        return NumericKind.MONEY, value, True
    if any(marker in lower for marker in _PERCENT):
        return NumericKind.PERCENT, value if "pct" in lower or "percentage_point" in lower else value * Decimal("100"), True
    if "day" in lower or "delay" in lower or "lead_time" in lower:
        return NumericKind.DAYS, value, False
    if unit and (any(marker in lower for marker in _QUANTITY) or lower not in {"scenario_count", "warning_count", "hard_violation_count"}):
        return NumericKind.QUANTITY, value, False
    if lower.endswith("count") or lower == "count":
        return NumericKind.COUNT, value, False
    return None


def _parse_decimal(raw: str) -> Decimal | None:
    if raw.count(".") > 1 or raw.count(",") > 1:
        normalized = raw.replace(".", "").replace(",", "")
    elif "." in raw or "," in raw:
        sep = "." if "." in raw else ","
        left, right = raw.split(sep)
        normalized = raw.replace(sep, "") if len(right) == 3 and len(left) <= 3 else raw.replace(sep, ".")
    else:
        normalized = raw
    try:
        return Decimal(normalized)
    except InvalidOperation:
        return None


def _normalize_unit(value: object) -> str | None:
    text = str(value).casefold()
    aliases = (("kg", "kg"), ("liters", "l"), ("litres", "l"), ("lít", "l"), ("cái", "cái"), ("ngày", "ngày"), ("days", "day"), ("sku", "sku"), ("nguyên liệu", "nguyên liệu"), ("ingredients", "ingredient"))
    for marker, normalized in aliases:
        if re.search(rf"\b{re.escape(marker)}\b", text):
            return normalized
    if re.search(r"\bg\b", text):
        return "g"
    if re.search(r"\bl\b", text):
        return "l"
    return None


def _optional_string(value: object) -> str | None:
    return str(value) if value is not None else None


def _vi(value: Decimal, precision: int = 2) -> str:
    rendered = f"{value:,.{precision}f}".rstrip("0").rstrip(".")
    return rendered.replace(",", "X").replace(".", ",").replace("X", ".")

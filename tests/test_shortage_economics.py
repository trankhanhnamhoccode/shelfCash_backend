from datetime import date

import pytest

from app.services.shortage_economics import build_shortage_economics


class Row:
    def __init__(self, ingredient_id, target_date, contributions):
        self.ingredient_id = ingredient_id
        self.target_date = target_date
        self.contributions = contributions


def _source(product, forecast, contribution):
    return {"product_id": product, "forecast_p50": forecast, "contribution_p50": contribution}


def test_derived_contribution_margin_shortage_cost_is_demand_weighted():
    rows = [
        Row("milk", date(2026, 8, 14), [_source("a", 100, 20)]),
        Row("milk", date(2026, 8, 14), [_source("b", 50, 25)]),
    ]
    result = build_shortage_economics(
        demand_rows=rows, product_prices={"a": 50_000, "b": 60_000},
        reference_costs={"milk": 100_000},
    )
    # recipe costs: a=20*100k/100=20k; b=25*100k/50=50k
    # (100*30k + 50*10k)/(20+25) = 77,777.777... / L
    item = result["milk"]
    assert item["source"] == "derived_contribution_margin"
    assert item["shortage_cost_per_unit"] == pytest.approx(3_500_000 / 45)


def test_incomplete_positive_demand_product_uses_fallback_not_partial_derivation():
    rows = [
        Row("milk", date(2026, 8, 14), [_source("complete", 10, 2)]),
        Row("milk", date(2026, 8, 14), [_source("missing-price", 10, 2)]),
    ]
    result = build_shortage_economics(
        demand_rows=rows, product_prices={"complete": 100}, reference_costs={"milk": 10},
    )
    assert result["milk"]["source"] == "supplier_replacement_fallback"
    assert "PRODUCT_PRICE_NOT_AVAILABLE" in result["milk"]["reason"]


def test_zero_demand_missing_price_does_not_invalidate_economics():
    rows = [
        Row("milk", date(2026, 8, 14), [_source("complete", 10, 2)]),
        Row("milk", date(2026, 8, 14), [_source("zero", 0, 0)]),
    ]
    result = build_shortage_economics(
        demand_rows=rows, product_prices={"complete": 100}, reference_costs={"milk": 10},
    )
    assert result["milk"]["source"] == "derived_contribution_margin"
    assert result["milk"]["shortage_cost_per_unit"] == pytest.approx(490)


def test_non_positive_margin_is_clamped_to_zero():
    rows = [Row("milk", date(2026, 8, 14), [_source("loss", 10, 2)])]
    result = build_shortage_economics(
        demand_rows=rows, product_prices={"loss": 10}, reference_costs={"milk": 100},
    )
    assert result["milk"]["shortage_cost_per_unit"] == 0
    assert "NON_POSITIVE_CONTRIBUTION_MARGIN" in result["milk"]["diagnostics"]

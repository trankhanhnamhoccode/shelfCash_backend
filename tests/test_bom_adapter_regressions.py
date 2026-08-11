from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models.business import IngredientModel, ProductModel, RecipeLineModel, RecipeVersionModel
from app.services.decision.adapters.bom_adapter import CoreBomAdapter


STORE_ID = "STORE_001"


def _forecast_run(horizon_days: int):
    return SimpleNamespace(
        cutoff_date=date(2026, 8, 11), horizon_days=horizon_days, model_version="test"
    )


def _prediction(product_id: str, product_name: str, target_date: date, horizon: int, p50: float, *, p25=None, p75=None):
    return SimpleNamespace(
        product_id=product_id,
        product_name=product_name,
        target_date=target_date,
        horizon=horizon,
        p25=p50 * 0.95 if p25 is None else p25,
        p50=p50,
        p75=p50 * 1.05 if p75 is None else p75,
        interval_lower=p50 * 0.95 if p25 is None else p25,
        interval_upper=p50 * 1.05 if p75 is None else p75,
        baseline_p50=p50,
        calibration_source="test",
        warnings_json="[]",
    )


def _add_recipe(session, product_id: str, version: int, effective_from: date, effective_to, lines):
    recipe_id = str(uuid4())
    session.add(
        RecipeVersionModel(
            recipe_version_id=recipe_id,
            store_id=STORE_ID,
            product_id=product_id,
            version=version,
            effective_from=effective_from,
            effective_to=effective_to,
            content_hash=f"recipe-{recipe_id}",
            source="test",
            yield_quantity=Decimal("1"),
            yield_unit="unit",
            process_loss_rate=Decimal("0"),
        )
    )
    for ingredient_id, quantity, unit in lines:
        session.add(
            RecipeLineModel(
                recipe_line_id=str(uuid4()),
                recipe_version_id=recipe_id,
                ingredient_id=ingredient_id,
                quantity=Decimal(str(quantity)),
                unit=unit,
            )
        )


def _add_product(session, name: str):
    product_id = str(uuid4())
    session.add(
        ProductModel(
            product_id=product_id,
            store_id=STORE_ID,
            product=name,
            normalized_name=name.lower().replace(" ", "-"),
            active=True,
            source="test",
        )
    )
    return product_id


def _add_ingredient(session, name: str, unit: str):
    ingredient_id = str(uuid4())
    session.add(
        IngredientModel(
            ingredient_id=ingredient_id,
            store_id=STORE_ID,
            ingredient=name,
            normalized_name=name.lower().replace(" ", "-"),
            base_unit=unit,
            active=True,
            source="test",
        )
    )
    return ingredient_id


def _expand(session, predictions):
    return CoreBomAdapter(session).expand(STORE_ID, _forecast_run(len(predictions)), predictions)


def test_horizon_seven_does_not_duplicate_matcha_daily_demand(client):
    session_factory = client.app.state.session_factory
    with session_factory() as session:
        product_id = _add_product(session, "Matcha latte")
        ingredient_id = _add_ingredient(session, "Matcha", "kg")
        _add_recipe(session, product_id, 1, date(2026, 1, 1), None, [(ingredient_id, 0.008, "kg")])
        session.commit()

        predictions = [
            _prediction(
                product_id, "Matcha latte", date(2026, 8, 12 + day), day + 1, 22.057811,
                p25=20.907678, p75=24.263924,
            )
            for day in range(7)
        ]
        rows = _expand(session, predictions)

    first_day = next(row for row in rows if row["target_date"] == date(2026, 8, 12))
    assert first_day["p50"] == pytest.approx(0.176462488)
    assert first_day["p25"] == pytest.approx(0.167261424)
    assert first_day["p75"] == pytest.approx(0.194111392)
    assert first_day["p50"] != pytest.approx(1.235237416)
    assert len(first_day["contributions"]) == 1
    assert first_day["contributions"][0]["contribution_p50"] == pytest.approx(0.176462488)


@pytest.mark.parametrize("horizon_days", [1, 3, 7])
def test_target_day_demand_is_independent_of_forecast_horizon(client, horizon_days):
    session_factory = client.app.state.session_factory
    with session_factory() as session:
        product_id = _add_product(session, f"Matcha-{horizon_days}")
        ingredient_id = _add_ingredient(session, f"Matcha-{horizon_days}", "kg")
        _add_recipe(session, product_id, 1, date(2026, 1, 1), None, [(ingredient_id, 0.008, "kg")])
        session.commit()
        rows = _expand(
            session,
            [_prediction(product_id, "Matcha", date(2026, 8, 12 + day), day + 1, 22.057811) for day in range(horizon_days)],
        )

    first_day = next(row for row in rows if row["target_date"] == date(2026, 8, 12))
    assert first_day["p50"] == pytest.approx(0.176462488)


def test_recipe_frame_keeps_multiple_ingredient_lines(client):
    session_factory = client.app.state.session_factory
    with session_factory() as session:
        product_id = _add_product(session, "Three ingredients")
        ingredients = [
            _add_ingredient(session, "Ingredient A", "kg"),
            _add_ingredient(session, "Ingredient B", "ml"),
            _add_ingredient(session, "Ingredient C", "g"),
        ]
        _add_recipe(session, product_id, 1, date(2026, 1, 1), None, [
            (ingredients[0], 0.10, "kg"),
            (ingredients[1], 0.20, "ml"),
            (ingredients[2], 1, "g"),
        ])
        session.commit()
        rows = _expand(session, [_prediction(product_id, "Three ingredients", date(2026, 8, 12), 1, 10)])

    assert {row["ingredient_id"] for row in rows} == set(ingredients)
    demand_by_ingredient = {row["ingredient_id"]: row["p50"] for row in rows}
    assert demand_by_ingredient == pytest.approx({
        ingredients[0]: 1.0,
        ingredients[1]: 0.002,
        ingredients[2]: 0.01,
    })


def test_effective_recipe_version_is_selected_per_target_date(client):
    session_factory = client.app.state.session_factory
    with session_factory() as session:
        product_id = _add_product(session, "Effective recipe")
        ingredient_id = _add_ingredient(session, "Milk", "kg")
        _add_recipe(session, product_id, 1, date(2026, 1, 1), date(2026, 8, 14), [(ingredient_id, 0.1, "kg")])
        _add_recipe(session, product_id, 2, date(2026, 8, 15), None, [(ingredient_id, 0.2, "kg")])
        session.commit()
        rows = _expand(session, [
            _prediction(product_id, "Effective recipe", date(2026, 8, 12 + day), day + 1, 10)
            for day in range(7)
        ])

    assert [row["p50"] for row in rows] == pytest.approx([1.0, 1.0, 1.0, 2.0, 2.0, 2.0, 2.0])
    assert [row["contributions"][0]["recipe_version"] for row in rows] == ["1", "1", "1", "2", "2", "2", "2"]


def test_multiple_products_can_contribute_to_the_same_ingredient(client):
    session_factory = client.app.state.session_factory
    with session_factory() as session:
        ingredient_id = _add_ingredient(session, "Shared milk", "kg")
        product_a = _add_product(session, "Latte A")
        product_b = _add_product(session, "Latte B")
        _add_recipe(session, product_a, 1, date(2026, 1, 1), None, [(ingredient_id, 0.1, "kg")])
        _add_recipe(session, product_b, 1, date(2026, 1, 1), None, [(ingredient_id, 0.2, "kg")])
        session.commit()
        rows = _expand(session, [
            _prediction(product_a, "Latte A", date(2026, 8, 12), 1, 10),
            _prediction(product_b, "Latte B", date(2026, 8, 12), 1, 10),
        ])

    assert len(rows) == 1
    assert rows[0]["p50"] == pytest.approx(3.0)
    assert len(rows[0]["contributions"]) == 2


def test_orange_juice_regression_value_is_not_multiplied_by_horizon(client):
    session_factory = client.app.state.session_factory
    with session_factory() as session:
        product_id = _add_product(session, "Orange juice")
        ingredient_id = _add_ingredient(session, "Orange", "kg")
        _add_recipe(session, product_id, 1, date(2026, 1, 1), None, [(ingredient_id, 0.3, "kg")])
        session.commit()
        rows = _expand(session, [
            _prediction(product_id, "Orange juice", date(2026, 8, 12 + day), day + 1, 22.484244)
            for day in range(7)
        ])

    first_day = next(row for row in rows if row["target_date"] == date(2026, 8, 12))
    assert first_day["p50"] == pytest.approx(6.7452732)
    assert first_day["p50"] != pytest.approx(47.2169124)

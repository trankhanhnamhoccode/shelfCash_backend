from __future__ import annotations

from collections.abc import Iterable
from datetime import timedelta

import pandas as pd

from shelfcash_core.bom.adapter import adapt_unit_conversions
from shelfcash_core.bom.contracts import IngredientDemandPackage
from shelfcash_core.bom.units import UnitConverter, normalize_unit
from shelfcash_core.exceptions import InventoryValidationError
from shelfcash_core.inventory.contracts import (
    ConsequenceCostAssumption,
    InboundDelivery,
    InventoryDemandLine,
    InventoryDemandScenario,
    InventoryLot,
    PlannedInboundDelivery,
    WasteEvent,
)
from shelfcash_core.scenario.contracts import IngredientDemandScenarioBundle


def quantile_inventory_scenarios(
    package: IngredientDemandPackage,
    *,
    allow_incomplete: bool = False,
) -> list[InventoryDemandScenario]:
    if not package.is_complete and not allow_incomplete:
        raise InventoryValidationError(
            "IngredientDemandPackage không complete.",
            code="INCOMPLETE_INGREDIENT_DEMAND",
            details={"issues": [issue.model_dump(mode="json") for issue in package.issues]},
        )
    definitions = (
        ("LOW_P25", "p25"),
        ("MEDIAN_P50", "p50"),
        ("HIGH_P75", "p75"),
    )
    scenarios: list[InventoryDemandScenario] = []
    for scenario_id, field_name in definitions:
        scenarios.append(
            InventoryDemandScenario(
                scenario_id=scenario_id,
                probability_weight=None,
                simulation_start_date=package.forecast_date + timedelta(days=1),
                simulation_end_date=package.forecast_date
                + timedelta(days=package.forecast_horizon),
                lines=[
                    InventoryDemandLine(
                        scenario_id=scenario_id,
                        store_id=prediction.store_id,
                        ingredient_id=prediction.ingredient_id,
                        target_date=prediction.target_date,
                        quantity=float(getattr(prediction, field_name)),
                        unit=prediction.unit,
                    )
                    for prediction in package.predictions
                ],
                provenance={
                    "source": "IngredientDemandPackage",
                    "quantile_field": field_name,
                    "forecast_model_version": package.forecast_model_version,
                    "quantile_is_probability": False,
                },
                warnings=package.warnings,
            )
        )
    return scenarios


def advanced_inventory_scenarios(
    bundle: IngredientDemandScenarioBundle,
    *,
    allow_incomplete: bool = False,
) -> list[InventoryDemandScenario]:
    if not bundle.is_complete and not allow_incomplete:
        raise InventoryValidationError(
            "IngredientDemandScenarioBundle không complete.",
            code="INCOMPLETE_INGREDIENT_DEMAND",
        )
    return [
        InventoryDemandScenario(
            scenario_id=scenario.scenario_id,
            probability_weight=scenario.probability_weight,
            simulation_start_date=bundle.forecast_date + timedelta(days=1),
            simulation_end_date=bundle.forecast_date + timedelta(days=bundle.horizon),
            lines=[
                InventoryDemandLine(
                    scenario_id=scenario.scenario_id,
                    store_id=line.store_id,
                    ingredient_id=line.ingredient_id,
                    target_date=line.target_date,
                    quantity=line.quantity,
                    unit=line.unit,
                )
                for line in scenario.lines
            ],
            provenance={
                "source": "IngredientDemandScenarioBundle",
                "scenario_method": bundle.scenario_method,
                "forecast_model_version": bundle.forecast_model_version,
            },
            warnings=sorted(set(bundle.warnings) | set(scenario.warnings)),
        )
        for scenario in bundle.scenarios
    ]


def unit_converter_from_frame(unit_conversions: pd.DataFrame | None) -> UnitConverter:
    return UnitConverter(adapt_unit_conversions(unit_conversions))


def demand_target_units(
    scenario: InventoryDemandScenario,
) -> dict[tuple[str, str], str]:
    units: dict[tuple[str, str], str] = {}
    for line in scenario.lines:
        key = (line.store_id, line.ingredient_id)
        unit = normalize_unit(line.unit)
        existing = units.get(key)
        if existing is not None and existing != unit:
            raise InventoryValidationError(
                "Demand scenario có nhiều units cho cùng store-ingredient.",
                code="INVALID_INVENTORY_UNIT",
                details={"key": key, "units": sorted({existing, unit})},
            )
        units[key] = unit
    return units


def _destination_unit(
    store_id: str,
    ingredient_id: str,
    source_unit: str,
    target_units: dict[tuple[str, str], str],
    converter: UnitConverter,
) -> str:
    return target_units.get(
        (store_id, ingredient_id),
        converter.canonical_unit(ingredient_id, source_unit),
    )


def normalize_lots(
    lots: Iterable[InventoryLot],
    target_units: dict[tuple[str, str], str],
    converter: UnitConverter,
) -> list[InventoryLot]:
    output: list[InventoryLot] = []
    for lot in lots:
        destination = _destination_unit(
            lot.store_id, lot.ingredient_id, lot.unit, target_units, converter
        )
        factor = converter.conversion_factor(lot.ingredient_id, lot.unit, destination)
        output.append(
            lot.model_copy(
                update={
                    "quantity_remaining": lot.quantity_remaining * factor,
                    "unit": destination,
                    "unit_cost": (
                        None if lot.unit_cost is None else lot.unit_cost / factor
                    ),
                }
            )
        )
    return output


def normalize_inbound(
    deliveries: Iterable[InboundDelivery | PlannedInboundDelivery],
    target_units: dict[tuple[str, str], str],
    converter: UnitConverter,
) -> list[InboundDelivery | PlannedInboundDelivery]:
    output: list[InboundDelivery | PlannedInboundDelivery] = []
    for delivery in deliveries:
        destination = _destination_unit(
            delivery.store_id,
            delivery.ingredient_id,
            delivery.unit,
            target_units,
            converter,
        )
        factor = converter.conversion_factor(
            delivery.ingredient_id, delivery.unit, destination
        )
        output.append(
            delivery.model_copy(
                update={
                    "quantity": delivery.quantity * factor,
                    "unit": destination,
                    "unit_cost": (
                        None if delivery.unit_cost is None else delivery.unit_cost / factor
                    ),
                }
            )
        )
    return output


def normalize_waste_events(
    events: Iterable[WasteEvent],
    target_units: dict[tuple[str, str], str],
    converter: UnitConverter,
) -> list[WasteEvent]:
    output: list[WasteEvent] = []
    for event in events:
        destination = _destination_unit(
            event.store_id, event.ingredient_id, event.unit, target_units, converter
        )
        output.append(
            event.model_copy(
                update={
                    "quantity": converter.convert(
                        event.quantity,
                        event.unit,
                        destination,
                        ingredient_id=event.ingredient_id,
                    ),
                    "unit": destination,
                }
            )
        )
    return output


def normalize_cost_assumptions(
    assumptions: Iterable[ConsequenceCostAssumption],
    target_units: dict[tuple[str, str], str],
    converter: UnitConverter,
) -> list[ConsequenceCostAssumption]:
    output: list[ConsequenceCostAssumption] = []
    for assumption in assumptions:
        destination = _destination_unit(
            assumption.store_id,
            assumption.ingredient_id,
            assumption.unit,
            target_units,
            converter,
        )
        factor = converter.conversion_factor(
            assumption.ingredient_id, assumption.unit, destination
        )
        output.append(
            assumption.model_copy(
                update={
                    "unit": destination,
                    "holding_cost_per_unit_day": (
                        assumption.holding_cost_per_unit_day / factor
                    ),
                    "shortage_cost_per_unit": assumption.shortage_cost_per_unit / factor,
                    "expired_cost_per_unit": assumption.expired_cost_per_unit / factor,
                    "waste_cost_per_unit": assumption.waste_cost_per_unit / factor,
                    "capacity_quantity": (
                        None
                        if assumption.capacity_quantity is None
                        else assumption.capacity_quantity * factor
                    ),
                }
            )
        )
    return output

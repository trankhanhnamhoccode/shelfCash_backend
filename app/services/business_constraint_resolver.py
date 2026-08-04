from app.core.business_constraints import constraint_definition
from app.core.exceptions import PlanningError
from app.core.units import convert_quantity
from app.repositories.inventory_constraints import InventoryConstraintRepository


class BusinessConstraintResolver:
    def __init__(self, session):
        self.repository = InventoryConstraintRepository(session)

    def resolve_constraint(self, store_id, constraint_type, ingredient_id=None, as_of_date=None):
        kind, _ = self._definition(constraint_type)
        matches = self.repository.effective(store_id, kind, ingredient_id, as_of_date)
        if not matches:
            return None
        if len(matches) > 1:
            raise PlanningError("BUSINESS_CONSTRAINT_AMBIGUOUS", "Multiple business constraints are effective.", {
                "store_id": store_id, "ingredient_id": ingredient_id, "constraint_type": kind,
                "constraint_ids": [item.constraint_id for item in matches],
            })
        return matches[0]

    def resolve_quantity(self, store_id, constraint_type, ingredient_id, target_unit, as_of_date=None):
        kind, definition = self._definition(constraint_type)
        if definition.dimension != "quantity":
            self._dimension_mismatch(kind, definition.dimension, "quantity")
        item = self.resolve_constraint(store_id, kind, ingredient_id, as_of_date)
        if item is None:
            return None
        try:
            return convert_quantity(item.value, item.unit, target_unit)
        except Exception as exc:
            raise PlanningError("BUSINESS_CONSTRAINT_UNIT_CONVERSION_FAILED", "Business constraint unit conversion failed.", {
                "constraint_id": item.constraint_id, "constraint_type": kind, "dimension": definition.dimension,
                "from_unit": item.unit, "to_unit": target_unit,
            }) from exc

    def resolve_duration_days(self, store_id, constraint_type, ingredient_id=None, as_of_date=None):
        kind, definition = self._definition(constraint_type)
        if definition.dimension != "duration":
            self._dimension_mismatch(kind, definition.dimension, "duration")
        item = self.resolve_constraint(store_id, kind, ingredient_id, as_of_date)
        if item is None:
            return None
        if item.unit != "day":
            raise PlanningError("BUSINESS_CONSTRAINT_UNIT_INVALID", "Duration constraint is not stored in canonical days.",
                {"constraint_id": item.constraint_id, "constraint_type": kind, "dimension": "duration",
                 "unit": item.unit, "allowed_units": ["day"]})
        return item.value

    def resolve_ratio(self, store_id, constraint_type, ingredient_id=None, as_of_date=None):
        kind, definition = self._definition(constraint_type)
        if definition.dimension != "ratio":
            self._dimension_mismatch(kind, definition.dimension, "ratio")
        item = self.resolve_constraint(store_id, kind, ingredient_id, as_of_date)
        if item is None:
            return None
        if item.unit != "ratio" or item.value < 0 or item.value > 1:
            raise PlanningError("BUSINESS_CONSTRAINT_VALUE_INVALID", "Ratio constraint is not canonical.", {
                "constraint_id": item.constraint_id, "constraint_type": kind, "dimension": "ratio",
                "unit": item.unit, "value": str(item.value),
            })
        return item.value

    @staticmethod
    def _definition(constraint_type):
        try:
            return constraint_definition(constraint_type)
        except Exception as exc:
            raise PlanningError(exc.code, exc.message, exc.details) from exc

    @staticmethod
    def _dimension_mismatch(constraint_type, actual, requested):
        raise PlanningError("BUSINESS_CONSTRAINT_DIMENSION_MISMATCH", "Business constraint resolver dimension mismatch.", {
            "constraint_type": constraint_type, "dimension": actual, "requested_dimension": requested,
        })

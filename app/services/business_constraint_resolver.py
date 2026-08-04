from app.core.exceptions import PlanningError
from app.core.units import convert_quantity
from app.repositories.inventory_constraints import InventoryConstraintRepository


class BusinessConstraintResolver:
    def __init__(self, session):
        self.repository = InventoryConstraintRepository(session)

    def resolve_constraint(self, store_id, constraint_type, ingredient_id=None, as_of_date=None):
        matches = self.repository.effective(store_id, constraint_type, ingredient_id, as_of_date)
        if not matches:
            return None
        if len(matches) > 1:
            raise PlanningError("BUSINESS_CONSTRAINT_AMBIGUOUS", "Nhiều business constraint cùng hiệu lực.", {
                "store_id": store_id, "ingredient_id": ingredient_id, "constraint_type": constraint_type,
                "constraint_ids": [item.constraint_id for item in matches],
            })
        return matches[0]

    def resolve_quantity(self, store_id, constraint_type, ingredient_id, target_unit, as_of_date=None):
        item = self.resolve_constraint(store_id, constraint_type, ingredient_id, as_of_date)
        if item is None:
            return None
        try:
            return convert_quantity(item.value, item.unit, target_unit)
        except Exception as exc:
            code = "SAFETY_STOCK_UNIT_CONVERSION_FAILED" if constraint_type == "safety_stock" else "BUSINESS_CONSTRAINT_UNIT_INVALID"
            raise PlanningError(code, "Không thể chuyển đổi đơn vị business constraint.", {
                "constraint_id": item.constraint_id, "from_unit": item.unit, "to_unit": target_unit,
            }) from exc

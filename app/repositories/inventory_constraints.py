from datetime import date

from sqlalchemy import or_, select

from app.models.business import InventoryConstraintModel


class InventoryConstraintRepository:
    def __init__(self, session):
        self.session = session

    def effective(self, store_id, constraint_type, ingredient_id=None, as_of_date=None):
        day = as_of_date or date.today()
        return list(self.session.scalars(select(InventoryConstraintModel).where(
            InventoryConstraintModel.store_id == store_id,
            InventoryConstraintModel.constraint_type == constraint_type,
            InventoryConstraintModel.ingredient_id == ingredient_id,
            InventoryConstraintModel.superseded_by_constraint_id.is_(None),
            InventoryConstraintModel.effective_date <= day,
            or_(InventoryConstraintModel.end_date.is_(None), InventoryConstraintModel.end_date >= day),
        ).order_by(InventoryConstraintModel.version.desc())))

    def list(self, store_id, ingredient_id=None, constraint_type=None, as_of_date=None):
        query = select(InventoryConstraintModel).where(InventoryConstraintModel.store_id == store_id)
        if ingredient_id is not None:
            query = query.where(InventoryConstraintModel.ingredient_id == ingredient_id)
        if constraint_type is not None:
            query = query.where(InventoryConstraintModel.constraint_type == constraint_type)
        if as_of_date is not None:
            query = query.where(InventoryConstraintModel.effective_date <= as_of_date,
                or_(InventoryConstraintModel.end_date.is_(None), InventoryConstraintModel.end_date >= as_of_date))
        return list(self.session.scalars(query.order_by(InventoryConstraintModel.constraint_type, InventoryConstraintModel.ingredient_id, InventoryConstraintModel.version.desc())))

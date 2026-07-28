from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.business import RecipeLineModel, RecipeVersionModel


class RecipeRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_active(self, store_id: str, product_id: str, on_date: date) -> RecipeVersionModel | None:
        return self.session.scalar(select(RecipeVersionModel).where(
            RecipeVersionModel.store_id == store_id, RecipeVersionModel.product_id == product_id,
            RecipeVersionModel.effective_from <= on_date,
            (RecipeVersionModel.effective_to.is_(None) | (RecipeVersionModel.effective_to >= on_date)),
        ).order_by(RecipeVersionModel.version.desc()))

    def get_versions(self, store_id: str, product_id: str) -> list[RecipeVersionModel]:
        return list(self.session.scalars(select(RecipeVersionModel).where(RecipeVersionModel.store_id == store_id, RecipeVersionModel.product_id == product_id).order_by(RecipeVersionModel.version)))

    def lines(self, recipe_version_id: str) -> list[RecipeLineModel]:
        return list(self.session.scalars(select(RecipeLineModel).where(RecipeLineModel.recipe_version_id == recipe_version_id)))

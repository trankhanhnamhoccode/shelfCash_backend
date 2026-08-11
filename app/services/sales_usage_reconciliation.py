"""Reconcile derived daily usage from canonical sales without touching observed usage."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select

from app.models.business import (
    ProductBundleLineModel, ProductModel, RecipeLineModel, RecipeVersionModel,
    SalesDailyModel, UsageDailyModel,
)


DERIVED_USAGE_SOURCE = "reconstructed_from_sales"


def is_derived_usage_source(source: str | None) -> bool:
    return source == DERIVED_USAGE_SOURCE


def reconcile_usage_from_sales(session, store_id: str, dates) -> list[dict]:
    """Rebuild only derived usage for affected dates from persisted sales state.

    Explicit/observed usage rows are authoritative and are never changed.
    """
    warnings: list[dict] = []
    for day in sorted(set(dates)):
        totals: dict[tuple[str, str], Decimal] = defaultdict(lambda: Decimal(0))
        sales = list(session.scalars(select(SalesDailyModel).where(
            SalesDailyModel.store_id == store_id, SalesDailyModel.date == day
        )))
        for sale in sales:
            product = session.scalar(select(ProductModel).where(
                ProductModel.store_id == store_id,
                ProductModel.product_id == sale.product_id,
            ))
            if product is None:
                continue
            demand_products = [(product, Decimal(1))]
            if product.item_type == "combo":
                bundle = list(session.execute(
                    select(ProductBundleLineModel, ProductModel)
                    .join(ProductModel, ProductModel.product_id == ProductBundleLineModel.component_product_id)
                    .where(
                        ProductBundleLineModel.store_id == store_id,
                        ProductBundleLineModel.combo_product_id == product.product_id,
                    )
                    .order_by(ProductBundleLineModel.position)
                ))
                demand_products = [(component, Decimal(bundle_line.quantity)) for bundle_line, component in bundle]
            for demand_product, multiplier in demand_products:
                recipe = session.scalar(select(RecipeVersionModel).where(
                    RecipeVersionModel.store_id == store_id,
                    RecipeVersionModel.product_id == demand_product.product_id,
                    RecipeVersionModel.effective_from <= day,
                    (RecipeVersionModel.effective_to.is_(None)) | (RecipeVersionModel.effective_to >= day),
                ).order_by(RecipeVersionModel.version.desc()))
                if recipe is None:
                    warnings.append({"code": "RECIPE_NOT_FOUND", "product_id": demand_product.product_id, "sale_date": day.isoformat()})
                    continue
                for line in session.scalars(select(RecipeLineModel).where(
                    RecipeLineModel.recipe_version_id == recipe.recipe_version_id
                )):
                    totals[(line.ingredient_id, line.unit)] += Decimal(sale.quantity) * multiplier * Decimal(line.quantity)

        existing_rows = list(session.scalars(select(UsageDailyModel).where(
            UsageDailyModel.store_id == store_id, UsageDailyModel.date == day
        )))
        existing = {row.ingredient_id: row for row in existing_rows}
        for (ingredient_id, unit), quantity in totals.items():
            row = existing.get(ingredient_id)
            if row is None:
                session.add(UsageDailyModel(
                    usage_record_id=str(uuid4()), store_id=store_id, date=day,
                    ingredient_id=ingredient_id, quantity=quantity, unit=unit,
                    source=DERIVED_USAGE_SOURCE,
                ))
            elif is_derived_usage_source(row.source):
                row.quantity, row.unit = quantity, unit
        for row in existing_rows:
            if is_derived_usage_source(row.source) and row.ingredient_id not in {key[0] for key in totals}:
                row.quantity = Decimal(0)
    return warnings

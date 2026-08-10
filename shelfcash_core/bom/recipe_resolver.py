from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from shelfcash_core.bom.contracts import BOMIssue, RecipeRecord
from shelfcash_core.exceptions import RecipeVersionError


@dataclass(frozen=True)
class RecipeResolution:
    records: tuple[RecipeRecord, ...]
    issue: BOMIssue | None = None

    @property
    def found(self) -> bool:
        return bool(self.records)


class RecipeResolver:
    """Resolve exactly one active recipe version for each product target date."""

    def __init__(self, records: list[RecipeRecord]) -> None:
        by_product: dict[str, list[RecipeRecord]] = defaultdict(list)
        for record in records:
            by_product[record.product_id].append(record)
        self._by_product = {
            product_id: tuple(product_records)
            for product_id, product_records in by_product.items()
        }

    def resolve(
        self,
        product_id: str,
        target_date: date,
        *,
        store_id: str | None = None,
    ) -> RecipeResolution:
        active = [
            record
            for record in self._by_product.get(product_id, ())
            if record.effective_from <= target_date
            and (record.effective_to is None or target_date <= record.effective_to)
        ]
        if not active:
            details: dict[str, object] = {
                "product_id": product_id,
                "target_date": target_date.isoformat(),
            }
            if store_id is not None:
                details["store_id"] = store_id
            return RecipeResolution(
                records=(),
                issue=BOMIssue(
                    code="MISSING_RECIPE",
                    message=(
                        f"Không có recipe active cho product={product_id} "
                        f"tại {target_date.isoformat()}."
                    ),
                    details=details,
                    recoverable=True,
                    suggested_action="Bổ sung recipe version bao phủ target_date.",
                ),
            )

        versions = {
            (
                record.recipe_id,
                record.recipe_version,
                record.effective_from,
                record.effective_to,
            )
            for record in active
        }
        if len(versions) != 1:
            raise RecipeVersionError(
                f"Có nhiều recipe versions active cho product={product_id} "
                f"tại {target_date.isoformat()}.",
                details={
                    "product_id": product_id,
                    "target_date": target_date.isoformat(),
                    "store_id": store_id,
                    "active_versions": [
                        {
                            "recipe_id": recipe_id,
                            "recipe_version": version,
                            "effective_from": effective_from.isoformat(),
                            "effective_to": (
                                effective_to.isoformat()
                                if effective_to is not None
                                else None
                            ),
                        }
                        for recipe_id, version, effective_from, effective_to in sorted(
                            versions,
                            key=lambda item: (
                                item[0],
                                item[1],
                                item[2],
                                item[3] or date.max,
                            ),
                        )
                    ],
                },
            )

        ordered = tuple(
            sorted(
                active,
                key=lambda record: (
                    record.ingredient_id,
                    record.ingredient_unit,
                    record.ingredient_quantity,
                ),
            )
        )
        return RecipeResolution(records=ordered)

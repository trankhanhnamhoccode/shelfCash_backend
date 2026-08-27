"""Deterministic canonical-ingredient expiry classification.

Expiry is a property of the material, not a missing-value convention on a lot.
This service intentionally uses canonical ``ingredient_id`` only; aliases have
already been resolved before rows reach persistence.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.names import normalize_lookup_name
from app.models.business import IngredientModel, InventoryLotModel, SupplierIngredientTermModel

MAX_INFERRED_NON_EXPIRY_RATIO = 0.50

# These are material names, not broad food-name keywords.  In particular this
# list deliberately excludes sugar, salt, flour and every food ingredient.
_NON_EXPIRY_HINTS = (
    "ly", "ly nhua", "coc", "coc nhua", "ong hut", "nap", "nap ly",
    "tui", "tui giay", "tui nhua", "hop", "hop nhua", "hop giay", "bao bi",
    "khan giay", "muong", "thia",
)


def is_conservative_non_expiry_material(name: str) -> bool:
    normalized = normalize_lookup_name(name)
    return any(normalized == hint or normalized.startswith(f"{hint} ") for hint in _NON_EXPIRY_HINTS)


class IngredientExpiryClassificationService:
    """Recompute inferred classifications after expiry-relevant imports."""

    def __init__(self, session: Session):
        self.session = session

    def declare(self, ingredient: IngredientModel, mode: object) -> None:
        if mode in (None, ""):
            return
        value = str(mode).strip().lower()
        if value not in {"required", "not_required", "unknown"}:
            raise ValueError("expiry_tracking_mode must be required, not_required, or unknown")
        ingredient.expiry_tracking_mode = value
        ingredient.expiry_tracking_source = "declared"

    def recompute(self, store_id: str) -> list[dict]:
        ingredients = list(self.session.scalars(select(IngredientModel).where(IngredientModel.store_id == store_id)))
        if not ingredients:
            return []
        ids = [item.ingredient_id for item in ingredients]
        lot_expiry_ids = set(self.session.scalars(select(InventoryLotModel.ingredient_id).where(
            InventoryLotModel.store_id == store_id, InventoryLotModel.ingredient_id.in_(ids),
            InventoryLotModel.expiry_date.is_not(None),
        )))
        missing_lot_rows = list(self.session.execute(select(InventoryLotModel.ingredient_id, InventoryLotModel.batch_code).where(
            InventoryLotModel.store_id == store_id, InventoryLotModel.ingredient_id.in_(ids),
            InventoryLotModel.expiry_date.is_(None),
        )))
        lot_missing_ids = {row.ingredient_id for row in missing_lot_rows}
        missing_batches: dict[str, list[str | None]] = {}
        for row in missing_lot_rows:
            missing_batches.setdefault(row.ingredient_id, []).append(row.batch_code)
        term_expiry_ids = set(self.session.scalars(select(SupplierIngredientTermModel.ingredient_id).where(
            SupplierIngredientTermModel.store_id == store_id, SupplierIngredientTermModel.ingredient_id.in_(ids),
            SupplierIngredientTermModel.active.is_(True), SupplierIngredientTermModel.shelf_life_days.is_not(None),
        )))
        evidence_ids = lot_expiry_ids | term_expiry_ids
        # A declared ``not_required`` value is known non-expiry material, but
        # still belongs in the store-level no-expiry population.  Only an
        # explicit *required* declaration removes it from the denominator's
        # candidate set; declared values are never changed below.
        candidates = [item for item in ingredients if not (
            item.expiry_tracking_source == "declared" and item.expiry_tracking_mode == "required"
        ) and item.ingredient_id not in evidence_ids]
        ratio = len(candidates) / len(ingredients)
        excessive = ratio > MAX_INFERRED_NON_EXPIRY_RATIO
        warnings: list[dict] = []
        if excessive:
            warnings.append({"code": "EXCESSIVE_NON_EXPIRY_INGREDIENT_RATIO", "details": {
                "unique_ingredient_count": len(ingredients), "candidate_no_expiry_count": len(candidates),
                "ratio": ratio, "threshold": MAX_INFERRED_NON_EXPIRY_RATIO,
            }})
        for item in ingredients:
            if item.expiry_tracking_source == "declared":
                continue
            has_evidence = item.ingredient_id in evidence_ids
            if has_evidence:
                item.expiry_tracking_mode = "required"
            elif not excessive and is_conservative_non_expiry_material(item.ingredient):
                item.expiry_tracking_mode = "not_required"
            else:
                item.expiry_tracking_mode = "unknown"
            item.expiry_tracking_source = "inferred"
            if has_evidence and item.ingredient_id in lot_missing_ids:
                warnings.append({"code": "INCONSISTENT_EXPIRY_TRACKING", "details": {
                    "ingredient_id": item.ingredient_id, "ingredient_name": item.ingredient,
                    "reason": "canonical_ingredient_has_expiry_evidence_and_missing_expiry_lot",
                    "has_expiry_lot": item.ingredient_id in lot_expiry_ids,
                    "has_shelf_life_term": item.ingredient_id in term_expiry_ids,
                    "missing_batch_ids": missing_batches.get(item.ingredient_id, [])[:10],
                }})
        return warnings

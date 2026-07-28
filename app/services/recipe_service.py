import hashlib
import json
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from app.core.exceptions import ValidationError
from app.core.units import normalize_unit, validate_compatible
from app.models.business import RecipeLineModel, RecipeVersionModel
from app.repositories.business import CatalogRepository
from app.repositories.recipes import RecipeRepository


class RecipeVersionService:
    def __init__(self, repository: RecipeRepository):
        self.repository = repository
        self.catalog = CatalogRepository(repository.session)

    @staticmethod
    def compute_content_hash(product_id: str, effective_from: date, lines: list[dict]) -> str:
        canonical = sorted(
            [{"ingredient_id": x["ingredient_id"], "quantity": str(Decimal(str(x["quantity"])).normalize()), "unit": normalize_unit(x["unit"])} for x in lines],
            key=lambda x: x["ingredient_id"],
        )
        raw = json.dumps({"product_id": product_id, "effective_from": effective_from.isoformat(), "lines": canonical}, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()

    def create_version(self, store_id: str, product_id: str, effective_from: date, lines: list[dict], source: str = "manual") -> RecipeVersionModel:
        product = self.catalog.get_product(store_id, product_id)
        if product is None:
            raise ValidationError("Product phải thuộc store.")
        ids = [x["ingredient_id"] for x in lines]
        if len(ids) != len(set(ids)):
            raise ValidationError("Không được duplicate ingredient trong recipe.")
        if not lines:
            raise ValidationError("Recipe phải có ít nhất một line.")
        for line in lines:
            ingredient = self.catalog.get_ingredient(store_id, line["ingredient_id"])
            if ingredient is None:
                raise ValidationError("Ingredient phải thuộc cùng store với product.")
            if Decimal(str(line["quantity"])) <= 0:
                raise ValidationError("Recipe quantity phải lớn hơn 0.")
            validate_compatible(line["unit"], ingredient.base_unit)
        content_hash = self.compute_content_hash(product_id, effective_from, lines)
        versions = self.repository.get_versions(store_id, product_id)
        for existing in versions:
            if existing.content_hash == content_hash:
                return existing
            if existing.effective_from >= effective_from:
                raise ValidationError("Effective date gây overlapping hoặc backdated recipe.")
        previous = versions[-1] if versions else None
        if previous and previous.effective_to is None:
            previous.effective_to = effective_from - timedelta(days=1)
        model = RecipeVersionModel(
            recipe_version_id=str(uuid4()), store_id=store_id, product_id=product_id,
            version=(previous.version + 1 if previous else 1), effective_from=effective_from,
            content_hash=content_hash, source=source,
        )
        self.repository.session.add(model)
        self.repository.session.flush()
        for line in lines:
            self.repository.session.add(RecipeLineModel(
                recipe_line_id=str(uuid4()), recipe_version_id=model.recipe_version_id,
                ingredient_id=line["ingredient_id"], quantity=Decimal(str(line["quantity"])),
                unit=normalize_unit(line["unit"]),
            ))
        return model

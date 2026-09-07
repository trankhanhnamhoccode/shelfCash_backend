# Catalog and Recipe API

Checkpoint 3A1 repair aligns public routes with the API contract. `store_id` is
authoritative only in the path and is not repeated in write bodies.

## Implemented routes

```text
GET   /api/v1/stores/{store_id}/ingredients
POST  /api/v1/stores/{store_id}/ingredients
PATCH /api/v1/stores/{store_id}/ingredients/{ingredient_id}

GET   /api/v1/stores/{store_id}/products
POST  /api/v1/stores/{store_id}/products
PATCH /api/v1/stores/{store_id}/products/{product_id}

GET /api/v1/stores/{store_id}/products/{product_id}/recipe
PUT /api/v1/stores/{store_id}/products/{product_id}/recipe

GET /api/v1/stores/{store_id}/aliases
PUT /api/v1/stores/{store_id}/aliases
```

Alias PUT uses all-or-nothing additive bulk upsert:

```json
{
  "aliases": [
    {"ingredient_id": "...", "alias": "milk"},
    {"ingredient_id": "...", "alias": "sữa"}
  ]
}
```

An existing normalized alias for the same ingredient is reused. An alias owned
by another ingredient, a duplicate inside the request, or a cross-store
ingredient rejects the entire transaction.

All implemented routes use `X-ShelfCash-Key`, return `X-Request-ID`, and use the
shared error envelope. Cross-store resource access returns 404.

## Write examples

Ingredient create:

```json
{
  "ingredient": "Sữa tươi",
  "sku": "MILK_001",
  "base_unit": "lít",
  "active": true
}
```

Product update:

```json
{
  "version": 1,
  "product": "Sinh tố chuối",
  "price": 35000,
  "active": true
}
```

Recipe write:

```json
{
  "effective_from": "2026-07-28",
  "version": 1,
  "lines": [
    {"ingredient_id": "...", "quantity": "0.125", "unit": "kg"}
  ]
}
```

Ingredient/product POST, alias PUT, and recipe PUT support `Idempotency-Key`. Ingredient and
product PATCH use optimistic `version`; recipe PUT uses `version` as the
expected current recipe version (`0` when creating the first version).

Canonical units are `kg`, `g`, `lít`, `ml`, and `cái`. Recipe quantities remain
Decimal values and are converted to the ingredient base unit. Ingredient base
units cannot change after dependent recipe, supplier, inventory, usage, or
purchase data exists.

Import-created catalog entities are visible through these reads. Manual
entities use the same deterministic names and units used by later imports.

Recipe-version history (P1), supplier constraints, settings, calendar,
inventory, history, bootstrap/dashboard, forecast, planning, and purchase-order
APIs remain outside this checkpoint.

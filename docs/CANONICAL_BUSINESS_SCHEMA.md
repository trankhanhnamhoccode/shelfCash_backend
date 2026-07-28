# Canonical Business Schema

Checkpoint 2B1 establishes normalized, store-scoped business persistence. It does
not connect the import processor to these tables and adds no public endpoints.

## Relationships

```text
stores
├─ store_settings
├─ ingredients ── ingredient_aliases
│  ├─ recipe_lines ── recipe_versions ── products
│  ├─ supplier_ingredient_terms ── suppliers
│  ├─ inventory_lots ── inventory_movements
│  ├─ usage_daily
│  └─ purchase_receipts
├─ sales_daily ── products
└─ calendar_features

import_jobs/import_sheet_profiles ── provenance links on imported business rows
```

## Source of truth and isolation

Business tables, not import JSON or frontend state, are the future source of
truth. Every resource carries `store_id`; repositories resolve an ID together
with its store and validate cross-store links before writing. Migrations and the
existing seed create no business/demo rows.

## Provenance

Import-capable rows retain `import_id`/`source_import_id`,
`profile_id`/`source_profile_id`, and `source_row_hash`. Composite unique
constraints provide the retry boundary for Checkpoint 2B2. An empty-file hash is
never a row identity.

## Units and names

Database units are `kg`, `g`, `lít`, `ml`, and `cái`. Aliases are normalized
deterministically; only kg↔g and lít↔ml conversions are supported with Decimal.
Mass, volume, and count are never converted across dimensions. Display names
preserve meaningful Unicode characters while matching keys use NFC, trimmed,
collapsed whitespace and case folding. There is no fuzzy or Qwen resolution.

Entity resolution order is explicit ID, exact SKU where applicable, exact alias,
then exact normalized name. Creation occurs only when `create_if_missing` is
explicitly enabled; imported entities use `source="import"`.

## Recipes and inventory

Recipes are immutable versions. Content hashes use sorted lines, so input order
does not affect identity. A changed recipe closes the previous range and creates
the next version; duplicate ingredients, overlaps, cross-store lines, nonpositive
quantities, and incompatible unit dimensions are rejected.

Inventory is movement-backed. Balance is calculated explicitly as
`SUM(quantity_delta)`; ORM properties do not issue hidden queries. Purchase
receipts are history only and do not automatically create inventory movements.

## Deliberately deferred

Checkpoint 2B1 does not write business rows during import, reconstruct usage,
create receipt movements, expose catalog/inventory/history APIs, or implement
forecasting, planning, optimization, recommendations, or purchase orders.
Checkpoint 2B2 owns canonical row hashing, deterministic import resolution and
transactional writes from completed imports into this schema.

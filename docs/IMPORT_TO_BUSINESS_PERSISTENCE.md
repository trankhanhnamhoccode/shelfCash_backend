# Import-to-Business Persistence

Checkpoint 2B2 connects the existing confirmed import process to canonical
business tables without adding public business endpoints.

## End-to-end flow

```text
normalized import aggregate
→ canonical normalization and deterministic validation
→ sheet-specific write plan
→ one database transaction for entities, business rows, result, issues,
  business summary, completed status, legacy cache, and audit
→ best-effort compatibility JSON export after commit
```

The policy is all-or-nothing. A deterministic validation or database write error
rolls back every business write. A separate transaction marks the import failed,
persists a safe issue, and writes one aggregate failure audit.

## Sheet mapping

| Sheet type | Business tables |
|---|---|
| `inventory` | ingredients, optional suppliers, inventory_lots, inventory_movements |
| `sales_history` | products, sales_daily |
| `usage_history` | ingredients, usage_daily |
| `recipes` | products, ingredients, recipe_versions, recipe_lines |
| `purchase_history` | ingredients, suppliers, purchase_receipts |
| `supplier_constraints` | ingredients, suppliers, supplier_ingredient_terms |
| `calendar_features` | calendar_features |
| `business_constraints` | store_settings |

Unknown sheets confirmed as skipped write no business rows.

## Deterministic identity and resolution

Source-row identity is SHA-256 over stable UTF-8 JSON containing store, import,
profile, compatibility sheet ID, source row number, canonical sheet type and
normalized row content. Keys are sorted; dates use ISO and Decimal values use
stable strings. It never uses Python `hash()`, timestamps or raw file checksum.

Purchase `business_key_hash` excludes import identity and includes store, date,
resolved ingredient/supplier, quantity, canonical unit, cost, expiry and batch.
Thus exact receipts across imports are skipped while real batch/date/value
changes remain distinct.

Ingredient resolution is ID/SKU/alias/exact normalized name/create; product is
ID/SKU/name/create; supplier is ID/name/create. Writes always use the import
job’s store. There is no fuzzy or Qwen entity matching.

## Units and correction policies

Quantities use Decimal and canonical units `kg`, `g`, `lít`, `ml`, `cái`.
Only kg↔g and lít↔ml conversions are allowed; cross-dimension input fails.

Inventory first snapshots create a lot and one opening movement. Later snapshots
calculate `target - SUM(movements)` and add an adjustment only when nonzero.
Batch code is preferred; otherwise a hashed reconciliation identity is used.

Sales aggregates by store/date/product/promotion. Usage aggregates by
store/date/ingredient after unit conversion. A newer import replaces the
aggregate instead of adding to it. Conflicting sales prices in one aggregate are
rejected.

Recipes use deterministic, order-independent content hashes. Identical content
reuses the existing version; changed content closes the prior version and creates
the next. Supplier terms similarly reuse identical content and otherwise
deactivate the previous version.

Purchase history never creates inventory lots or movements. Calendar rows upsert
by store/date and deterministically calculate weekend from the date. Settings
upsert one row per store and increment version only when supported content
changes. No weather, budget remainder, recommendation or forecast is fabricated.

## Idempotency, compatibility and audit

Idempotency has three layers: completed-import guard, source provenance
constraints, and business natural keys. `import_jobs` stores
`business_persisted_at`, schema version `20260728_0004`, and an aggregate write
summary. Completion audit contains counts only—not raw rows, paths, prompts or
secrets.

Public `sheets`, `mappings`, `sheet_id`, statuses and canonical result shape are
unchanged. The database remains the result source of truth; filesystem JSON is a
best-effort compatibility artifact.

## Deferred work and technical debt

There are no catalog/inventory/history CRUD APIs, bootstrap/dashboard,
forecasting, planning, optimizer, recommendation or purchase-order endpoints.
Usage is not reconstructed from sales and receipt history does not affect stock.
SQLite relies on transactions and unique constraints; production PostgreSQL
should add row-level locking/retry policy around recipe version allocation and
inventory reconciliation.
# SKU variant and recipe identity

Products are variants: `product_id` is the internal identifier and a non-null
`sku` is resolved within its store. Product names are display data and are not
unique. Imports that omit SKU may use a product name only when it resolves to
exactly one product; ambiguous names fail with
`MISSING_SKU_FOR_DUPLICATE_NAME`. A reused SKU with conflicting product data
fails with `SKU_CONFLICT`.

The canonical `recipes` sheet accepts the additive `product_sku` field. Recipe
rows are grouped by the resolved product variant and effective date. Bootstrap
recipe summaries include `sku`, `effective_to`, and `components`; each
component contains `ingredient_id`, `ingredient`, `quantity`, and `unit`.

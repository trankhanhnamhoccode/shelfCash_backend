# Inventory constraint contract

Supplier terms and inventory/business constraints are separate sources of truth.

- `GET /api/v1/business-constraint-types` exposes the authoritative registry used by import validation, query normalization, resolvers, and OpenAPI tooling.
- `GET /api/v1/stores/{store_id}/supplier-constraints` returns supplier-specific MOQ, pack size, order unit, lead time, and cost. It does not expose or persist `safety_stock` or inventory capacity.
- `GET /api/v1/stores/{store_id}/inventory-constraints` returns versioned store/ingredient constraints. It supports `ingredient_id`, `constraint_type`, and `as_of_date` query filters.
- Frontends that render a combined procurement table join these responses by `ingredient_id`. There is no duplicated persistence and no procurement-settings aggregation route at present.

Example supplier item:

```json
{"constraint_id":"term-1","ingredient_id":"milk","supplier_id":"supplier-a","supplier":"Supplier A","unit_cost":25000,"moq":20,"pack_size":10,"order_unit":"case","lead_time_days":2,"unit":"lít","version":1,"active":true}
```

Example inventory constraint item:

```json
{"constraint_id":"constraint-1","ingredient_id":"milk","ingredient_name":"Sữa tươi","constraint_type":"safety_stock","value":12,"unit":"lít","effective_date":"2026-07-01","end_date":null,"version":1,"active":true}
```

Planner trace distinguishes missing from configured zero:

```json
{"configured_safety_stock":null,"effective_safety_stock":"0","fallback_policy":"ZERO_WITH_WARNING","maximum_stock":null,"unit":"lít"}
```

Missing configuration adds `SAFETY_STOCK_NOT_CONFIGURED`. A configured zero has `configured_safety_stock: "0"`, no fallback policy, and no missing warning.

## Type-dependent dimensions and canonical units

| Constraint types | Scope | Dimension | Canonical unit/value |
|---|---|---|---|
| `safety_stock`, `maximum_stock`, `minimum_stock`, `reorder_point` | ingredient | quantity | Existing physical units, compatible with the ingredient base unit |
| `shelf_life_target` | ingredient | duration | Positive integer days, stored as `day` (`day`, `days`, `d`, and `ngày` are accepted) |
| `service_level_target` | store or ingredient | ratio | `0..1 ratio`; `percent`, `percentage`, and `%` inputs are divided by 100 |
| `storage_capacity`, `warehouse_capacity` | store | physical capacity | A supported mass, volume, or count unit; mixed dimensions are not aggregated by planning |
| `maximum_storage_volume` | store | volume | `lít` or `ml` |
| `budget` | store | currency | Non-negative value stored as `VND` |

`store_closed_date` is not an inventory constraint. Import it through `calendar_features.is_store_closed`; a business-constraint row using that type returns `BUSINESS_CONSTRAINT_TYPE_UNSUPPORTED`.

The `constraint_type` filter on `GET /api/v1/stores/{store_id}/inventory-constraints` accepts registered aliases and normalizes them before querying. Unsupported values return `BUSINESS_CONSTRAINT_TYPE_UNSUPPORTED` rather than an empty list.

## Storage-capacity planning semantics

Planning resolves one effective store-level capacity as of the forecast cutoff date. The deterministic type priority is `maximum_storage_volume`, then `storage_capacity`, then `warehouse_capacity`. Multiple effective versions of the selected type remain an ambiguity error; types are never selected by database row order.

Capacity and projected inventory units are normalized before comparison (`lít`, `lit`, `litre`, `L` to `liter`; `kg` to `kilogram`; `cái`, `unit` to `piece`). The persisted `storage_capacity_trace` records the source constraint, configured and canonical values, evaluation status, reason, and peak details when evaluable.

- No effective constraint: `STORAGE_CAPACITY_NOT_CONFIGURED`.
- Configured capacity with mixed inventory dimensions or a dimension mismatch: `STORAGE_CAPACITY_DIMENSION_UNSUPPORTED`; planning continues and does not mark the strategy infeasible.
- Compatible single-dimension inventory over its peak capacity: `STORAGE_CAPACITY_EXCEEDED`; the trace includes peak date/value and excess quantity, and the strategy is infeasible.

Planning result GET endpoints return this persisted trace and do not re-run capacity evaluation. Ingredient-level `maximum_stock` remains independently enforced in every case.

## Shelf-life-aware replenishment

For ingredients with an effective `shelf_life_target`, planning limits replenishment against demand from the supplier's expected arrival date through `arrival + configured days - 1`. Actual inventory lots continue to use their recorded `expiry_date`; planning does not manufacture an expiry date for proposed inbound.

The planner may reduce pack count when the smaller candidate preserves shortage performance within that shelf-life window, remains at or above MOQ, and respects configured `minimum_stock`. Residual excess caused by commercial terms is reported as `SHELF_LIFE_OVERBUY_RISK` together with `MOQ_FORCED_OVERBUY` or `PACK_SIZE_FORCED_OVERBUY`. Per-ingredient decisions are persisted in `shelf_life_trace` and returned unchanged by both procurement-plan and legacy plan-run GET endpoints.

## Operational planning constraints

`minimum_stock` and `safety_stock` are overlapping operating thresholds. Planning uses `target_ending_inventory = max(effective_safety_stock, effective_minimum_stock)` and never adds the two. A minimum-stock shortfall adds `MINIMUM_STOCK_GAP`.

`reorder_point` controls timing, not order quantity. The planner records the first baseline day whose ending inventory position is at or below the threshold. If current inventory is already at or below it, the trigger is the cutoff date and the line records `REORDER_POINT_TRIGGERED`. Orders are created at cutoff, which is no later than a future trigger; an arrival after the trigger or projected stockout adds `URGENT_STOCKOUT_RISK`.

`service_level_target` is resolved ingredient-first with a store-level fallback. Each ingredient's achieved fill rate is evaluated after final re-simulation. A result below target adds `SERVICE_LEVEL_NOT_MET` and makes that strategy infeasible while the planning run remains completed. Requested strategies and their existing quantile mapping are never overridden. Trace fields record target/achieved service level and whether strategies were explicit or supplied by the request default.

## Budget source of truth

Planning selects one VND cap in this order: request `budget_override` (including an explicit zero), the remaining `budget_periods` amount for the cutoff month, an effective store-level `budget` business constraint, legacy `store_settings.monthly_budget`, then not configured. Sources are never added, prorated, or blended.

A budget period contributes `max(0, monthly_budget - reserved_budget - spent_budget)`. That remaining cutoff-month amount caps the whole planning run even when the forecast horizon crosses into another month. A request override is a run-scoped cap from cutoff through horizon end. Business constraints use the existing generic constraint currency metadata and must be canonical integer `VND`; mismatched currency is rejected.

`store_settings.monthly_budget` remains readable only as a deprecated legacy fallback and emits a server warning when selected. Persisted `budget_trace` records configuration status, selected source, value, currency, period bounds, and period policy. Zero is configured budget; null request override means continue through precedence.

## Inventory constraint write API

- `POST /api/v1/stores/{store_id}/inventory-constraints` creates the first version of a constraint family.
- `PATCH /api/v1/stores/{store_id}/inventory-constraints/{constraint_id}` creates a new version; it never mutates the prior value.
- `POST /api/v1/stores/{store_id}/inventory-constraints/{constraint_id}/deactivate` closes the effective period without deleting history.

All writes use the shared registry validator and enforce store/ingredient ownership. PATCH and deactivate require `expected_version`; a stale writer receives `VERSION_CONFLICT`. Idempotency keys follow the existing store+method+route convention. Successful operations emit create/version/correction/deactivate audit records.

Normal version updates require a later effective date and close the previous version on the preceding day. A same-date replacement must explicitly set `correction_mode=replace_same_effective_date`; the old row is retained and linked through `superseded_by_constraint_id`. Corrections are blocked with `BUSINESS_CONSTRAINT_CORRECTION_BLOCKED` when a completed planning run in the effective period contains matching constraint trace lineage. The current auth contract has one API-key administrator permission level, so no narrower correction role exists yet.

Budget requests may send `currency`; other constraints use `unit`. The response returns the canonical constraint plus version history metadata, including correction supersession links. Migration `20260804_0017` adds only `note` and correction lineage; existing values are unchanged.

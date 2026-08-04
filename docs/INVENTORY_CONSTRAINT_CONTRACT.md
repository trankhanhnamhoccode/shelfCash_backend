# Inventory constraint contract

Supplier terms and inventory/business constraints are separate sources of truth.

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

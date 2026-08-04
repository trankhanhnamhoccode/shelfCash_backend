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

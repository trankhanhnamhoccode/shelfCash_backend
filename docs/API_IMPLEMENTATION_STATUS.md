# API implementation status

Source of truth: ShelfCash API Contract v1.1-consolidated plus Menu Addendum
v1.0-menu.

- Contract operations: 53
- Application operations: 53
- Missing: 0
- Unexpected: 0
- Production complete: 47
- Explicitly model-blocked: 6
- Incomplete: 0
- Alembic head: `20260729_0008`

The three Menu Addendum operations are:

- `GET /api/v1/import-schemas`
- `GET /api/v1/stores/{store_id}/menu`
- `PUT /api/v1/stores/{store_id}/products/{product_id}/components`

Forecast and planning keep the explicit `MODEL_NOT_READY`/blocked contract.
No forecast points or planner recommendations are fabricated.

# Menu import and API

Revision `20260729_0008` extends products without creating a second Menu table.
Legacy products are backfilled as `single`; their `selling_unit` remains null
when there is no trustworthy source. New Menu imports require a valid product
unit. Combo membership is relational in `product_bundle_lines`.

The canonical `menu` schema, header rules, confirmation gate, normalization and
`GET /api/v1/import-schemas` all read the same registry. Confirmation requires
all source headers to map exactly once and all four core fields. Formula cells
are read only from cached workbook values. The backend does not run macros,
formulas, LibreOffice or workbook-provided commands.

Menu processing validates the entire batch first, upserts singles before
combos, resolves exact normalized names within the store, replaces bundle lines
and commits once. A component error rolls the whole import back. Reprocessing
the same import updates the same `(store_id, sku)` rows and does not duplicate
bundle lines.

`GET /api/v1/stores/{store_id}/menu` is the shared read model used by product
responses, import result and bootstrap. Its summary describes the whole store
before status, item-type and search filters. Combo `list_price` is recalculated
from current component prices; the stored combo `price` is not changed.

Product POST/PATCH and component PUT retain existing auth, store isolation,
idempotency and optimistic concurrency. Components are ordered by request
position. Combo recipes are rejected with `RECIPE_NOT_ALLOWED_FOR_COMBO`.

Combo sales remain one original sales row. Demand expands through the current
bundle lines and each component's effective recipe; it does not create
component sales or inventory movements.

## Known v1 limitations

- Bundle component history is not versioned.
- Editing components does not rewrite completed forecast runs.
- Historical usage is not silently rebuilt after component edits.
- Legacy products may have null `selling_unit` until explicitly updated.

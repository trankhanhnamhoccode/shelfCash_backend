# Inventory snapshot and supplier-term integrity

## Inventory sheet

An `inventory` row is a lot-level observation, not a purchase receipt.

- `snapshot_date` is required and means the local calendar day on which the
  quantity was observed/counting was performed.
- `received_date` is optional and means the actual date the lot was received.
  A blank value remains unknown; it is never inferred from `snapshot_date`,
  import time, expiry date, or purchase history.
- `batch_id` is required and is the stable lot identity together with store and
  ingredient. A batch-less row is rejected with `INVENTORY_BATCH_ID_REQUIRED`.
- `expiry_date`, `supplier_name`, `received_date`, `unit`, and
  `warehouse_name` are protected lot metadata. A later snapshot may adjust
  quantity, but a supplied mismatch is rejected with
  `INVENTORY_LOT_METADATA_CONFLICT`; it does not rewrite the lot.

Date-only `snapshot_date` is persisted as start-of-day in the store timezone,
converted to UTC for `InventoryMovement.occurred_at`. `created_at` remains the
backend processing time. Older snapshots than an existing lot event are
rejected with `INVENTORY_SNAPSHOT_OUT_OF_ORDER`.

For equal expiry dates, FEFO uses known receipt dates chronologically first.
Lots with unknown receipt date form a deterministic, stable-lot-ID group; no
synthetic age is invented.

## Supplier constraints sheet

Usable supplier terms require `unit_price` and `lead_time_days`. Missing values
are rejected as `UNIT_PRICE_NOT_CONFIGURED` or `LEAD_TIME_NOT_CONFIGURED`; a
missing field is never converted to zero. Explicit numeric zero remains a
separate, declared value where the domain allows it. `shelf_life_days` remains
optional and produces the existing shelf-life limitation rather than a fake
expiry date.

## Purchase history

`purchase_history` is `record_only`: it creates historical procurement records
but does not create inventory lots, movements, or current on-hand balance.
Actual inventory receipt uses the PO receipt / inventory receipt workflow.

## Legacy audit

Run the read-only audit after deployment:

```powershell
python scripts/audit_inventory_received_dates.py --json
```

It reports declared, unknown, definitely legacy snapshot-fallback, suspicious,
and unverifiable receipt-date provenance. It never rewrites historical data.

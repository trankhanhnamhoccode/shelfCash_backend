"""replace lot count timestamp with latest physical snapshot date

Revision ID: 20260826_0024
Revises: 20260821_0023
"""
from datetime import date, datetime, time

import sqlalchemy as sa
from alembic import op


revision = "20260826_0024"
down_revision = "20260821_0023"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("inventory_lots", sa.Column("snapshot_date", sa.Date(), nullable=True))

    # Preserve the business date of API-originated counts that predate this
    # migration.  The original timestamp cannot survive a date-only contract.
    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT lot_id, last_counted_at FROM inventory_lots "
        "WHERE last_counted_at IS NOT NULL"
    )).mappings()
    for row in rows:
        value = row["last_counted_at"]
        snapshot_date = value.date() if hasattr(value, "date") else date.fromisoformat(str(value)[:10])
        bind.execute(
            sa.text("UPDATE inventory_lots SET snapshot_date = :snapshot_date WHERE lot_id = :lot_id"),
            {"snapshot_date": snapshot_date, "lot_id": row["lot_id"]},
        )

    with op.batch_alter_table("inventory_lots") as batch:
        batch.drop_column("last_counted_at")


def downgrade():
    # The date-only contract cannot restore the original time of day.  Use
    # local midnight solely to make the prior schema readable; the following
    # 0023 downgrade remains intentionally refused for its own data rule.
    op.add_column("inventory_lots", sa.Column("last_counted_at", sa.DateTime(timezone=True), nullable=True))
    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT lot_id, snapshot_date FROM inventory_lots WHERE snapshot_date IS NOT NULL"
    )).mappings()
    for row in rows:
        value = row["snapshot_date"]
        snapshot_date = value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
        bind.execute(
            sa.text("UPDATE inventory_lots SET last_counted_at = :counted_at WHERE lot_id = :lot_id"),
            {"counted_at": datetime.combine(snapshot_date, time.min), "lot_id": row["lot_id"]},
        )
    with op.batch_alter_table("inventory_lots") as batch:
        batch.drop_column("snapshot_date")

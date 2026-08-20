"""separate inventory observation time from lot receipt time

Revision ID: 20260821_0023
Revises: 20260812_0022
"""

from alembic import op
import sqlalchemy as sa


revision = "20260821_0023"
down_revision = "20260812_0022"
branch_labels = None
depends_on = None


def upgrade():
    # Every pre-existing import lot was created before inventory input could
    # declare a receipt date.  Those values were populated from snapshot time,
    # so they are provably not receipt-date evidence and are nulled honestly.
    with op.batch_alter_table("inventory_lots") as batch:
        batch.drop_constraint("ck_inventory_lot_dates", type_="check")
        batch.alter_column("received_date", existing_type=sa.Date(), nullable=True)
        batch.add_column(sa.Column(
            "received_date_status", sa.String(length=32), nullable=False,
            server_default="legacy_unknown",
        ))
        batch.create_check_constraint(
            "ck_inventory_lot_dates",
            "expiry_date IS NULL OR received_date IS NULL OR expiry_date >= received_date",
        )
        batch.create_check_constraint(
            "ck_inventory_lot_received_date_status",
            "received_date_status IN ('declared', 'unknown', 'legacy_unknown')",
        )
        batch.create_check_constraint(
            "ck_inventory_lot_declared_received_date",
            "received_date_status != 'declared' OR received_date IS NOT NULL",
        )
    op.execute("UPDATE inventory_lots SET received_date = NULL, received_date_status = 'legacy_unknown' WHERE source = 'import'")
    op.execute("UPDATE inventory_lots SET received_date_status = 'declared' WHERE source = 'purchase_order'")

    # Historical import rows with a zero price/lead time cannot distinguish an
    # explicit business zero from the old missing-value fallback.  They remain
    # usable only after a source with authoritative fields re-declares them.
    with op.batch_alter_table("supplier_ingredient_terms") as batch:
        batch.add_column(sa.Column(
            "unit_price_status", sa.String(length=32), nullable=False,
            server_default="legacy_unknown",
        ))
        batch.add_column(sa.Column(
            "lead_time_status", sa.String(length=32), nullable=False,
            server_default="legacy_unknown",
        ))
        batch.create_check_constraint(
            "ck_supplier_term_unit_price_status",
            "unit_price_status IN ('declared', 'legacy_unknown')",
        )
        batch.create_check_constraint(
            "ck_supplier_term_lead_time_status",
            "lead_time_status IN ('declared', 'legacy_unknown')",
        )
    op.execute("UPDATE supplier_ingredient_terms SET unit_price_status = 'declared' WHERE unit_cost > 0 OR source = 'api'")
    op.execute("UPDATE supplier_ingredient_terms SET lead_time_status = 'declared' WHERE lead_time_days > 0 OR source = 'api'")


def downgrade():
    # Reverting would require inventing receipt dates for the newly valid NULL
    # state.  Refuse rather than corrupt receipt provenance.
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT COUNT(*) FROM inventory_lots WHERE received_date IS NULL")).scalar_one():
        raise RuntimeError("Cannot downgrade: inventory_lots contains unknown received_date values.")
    with op.batch_alter_table("supplier_ingredient_terms") as batch:
        batch.drop_constraint("ck_supplier_term_lead_time_status", type_="check")
        batch.drop_constraint("ck_supplier_term_unit_price_status", type_="check")
        batch.drop_column("lead_time_status")
        batch.drop_column("unit_price_status")
    with op.batch_alter_table("inventory_lots") as batch:
        batch.drop_constraint("ck_inventory_lot_declared_received_date", type_="check")
        batch.drop_constraint("ck_inventory_lot_received_date_status", type_="check")
        batch.drop_constraint("ck_inventory_lot_dates", type_="check")
        batch.alter_column("received_date", existing_type=sa.Date(), nullable=False)
        batch.create_check_constraint(
            "ck_inventory_lot_dates", "expiry_date IS NULL OR expiry_date >= received_date"
        )
        batch.drop_column("received_date_status")

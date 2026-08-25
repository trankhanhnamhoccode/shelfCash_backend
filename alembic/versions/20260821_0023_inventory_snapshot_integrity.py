"""inventory snapshot identity and unknown receipt-date integrity

Revision ID: 20260821_0023
Revises: 20260812_0022
"""
import sqlalchemy as sa
from alembic import op

revision="20260821_0023"
down_revision="20260812_0022"
branch_labels=None
depends_on=None

def upgrade():
    with op.batch_alter_table("inventory_lots") as batch:
        batch.drop_constraint("ck_inventory_lot_dates",type_="check")
        batch.alter_column("received_date",existing_type=sa.Date(),nullable=True)
        batch.add_column(sa.Column("received_date_status",sa.String(32),nullable=False,server_default="legacy_unknown"))
        batch.create_check_constraint("ck_inventory_lot_dates","expiry_date IS NULL OR received_date IS NULL OR expiry_date >= received_date")
    # The legacy inventory importer had no received_date input, so imported
    # dates were provably snapshot fallbacks and must not masquerade as facts.
    op.execute("UPDATE inventory_lots SET received_date = NULL, received_date_status = 'legacy_unknown' WHERE source = 'import'")
    op.execute("UPDATE inventory_lots SET received_date_status = 'declared' WHERE source = 'purchase_order'")

def downgrade():
    raise RuntimeError("Downgrade would require fabricating unknown received_date values.")

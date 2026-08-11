"""enforce canonical lot and external receipt identities

Revision ID: 20260812_0021
Revises: 20260810_0020
"""
import sqlalchemy as sa
from alembic import op


revision = "20260812_0021"
down_revision = "20260810_0020"
branch_labels = None
depends_on = None


def upgrade():
    # These partial indexes intentionally fail on dirty databases with duplicate
    # canonical keys.  They never merge or delete business records.
    op.create_index(
        "uq_inventory_lots_store_ingredient_batch_present", "inventory_lots",
        ["store_id", "ingredient_id", "batch_code"], unique=True,
        postgresql_where=sa.text("batch_code IS NOT NULL"),
        sqlite_where=sa.text("batch_code IS NOT NULL"),
    )
    op.create_index(
        "uq_purchase_receipts_external_identity", "purchase_receipts",
        ["store_id", "source", "external_record_id"], unique=True,
        postgresql_where=sa.text("external_record_id IS NOT NULL"),
        sqlite_where=sa.text("external_record_id IS NOT NULL"),
    )


def downgrade():
    op.drop_index("uq_purchase_receipts_external_identity", table_name="purchase_receipts")
    op.drop_index("uq_inventory_lots_store_ingredient_batch_present", table_name="inventory_lots")

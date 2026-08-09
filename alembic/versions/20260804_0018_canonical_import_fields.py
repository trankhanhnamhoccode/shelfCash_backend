"""persist remaining canonical import fields

Revision ID: 20260804_0018
Revises: 20260804_0017
"""
import sqlalchemy as sa
from alembic import op

revision="20260804_0018"
down_revision="20260804_0017"
branch_labels=None
depends_on=None


def upgrade():
    # A direct ADD preserves the expression-based scope/version index on SQLite;
    # batch recreation cannot reflect that index and would silently discard it.
    op.add_column("inventory_constraints", sa.Column("currency",sa.String(3)))
    with op.batch_alter_table("supplier_ingredient_terms") as batch:batch.add_column(sa.Column("available_delivery_days",sa.Text()))
    with op.batch_alter_table("inventory_lots") as batch:batch.add_column(sa.Column("warehouse_name",sa.String(255)))
    with op.batch_alter_table("usage_daily") as batch:
        batch.add_column(sa.Column("usage_source",sa.String(128)))
        batch.add_column(sa.Column("waste_quantity",sa.Numeric(20,6),nullable=False,server_default="0"))
    with op.batch_alter_table("recipe_versions") as batch:batch.add_column(sa.Column("yield_unit",sa.String(16)))
    with op.batch_alter_table("purchase_receipts") as batch:
        batch.add_column(sa.Column("total_cost",sa.Numeric(20,2)))
        batch.add_column(sa.Column("purchase_order_id",sa.String(128)))


def downgrade():
    with op.batch_alter_table("purchase_receipts") as batch:
        batch.drop_column("purchase_order_id");batch.drop_column("total_cost")
    with op.batch_alter_table("recipe_versions") as batch:batch.drop_column("yield_unit")
    with op.batch_alter_table("usage_daily") as batch:
        batch.drop_column("waste_quantity");batch.drop_column("usage_source")
    with op.batch_alter_table("inventory_lots") as batch:batch.drop_column("warehouse_name")
    with op.batch_alter_table("supplier_ingredient_terms") as batch:batch.drop_column("available_delivery_days")
    op.drop_column("inventory_constraints", "currency")

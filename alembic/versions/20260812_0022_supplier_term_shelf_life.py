"""exact supplier-term shelf-life snapshots

Revision ID: 20260812_0022
Revises: 20260812_0021
"""

from alembic import op
import sqlalchemy as sa

revision = "20260812_0022"
down_revision = "20260812_0021"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("supplier_ingredient_terms") as batch:
        batch.drop_constraint("ck_supplier_term_values", type_="check")
        batch.add_column(sa.Column("shelf_life_days", sa.Integer(), nullable=True))
        batch.create_check_constraint(
            "ck_supplier_term_values",
            "unit_cost >= 0 AND moq >= 0 AND pack_size > 0 AND lead_time_days >= 0 "
            "AND (shelf_life_days IS NULL OR shelf_life_days >= 0)",
        )
    with op.batch_alter_table("purchase_order_lines") as batch:
        batch.add_column(sa.Column("shelf_life_days", sa.Integer(), nullable=True))


def downgrade():
    with op.batch_alter_table("purchase_order_lines") as batch:
        batch.drop_column("shelf_life_days")
    with op.batch_alter_table("supplier_ingredient_terms") as batch:
        batch.drop_constraint("ck_supplier_term_values", type_="check")
        batch.drop_column("shelf_life_days")
        batch.create_check_constraint(
            "ck_supplier_term_values",
            "unit_cost >= 0 AND moq >= 0 AND pack_size > 0 AND lead_time_days >= 0",
        )

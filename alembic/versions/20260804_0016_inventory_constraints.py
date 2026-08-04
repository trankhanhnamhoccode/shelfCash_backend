"""add versioned inventory and business constraints

Revision ID: 20260804_0016
Revises: 20260803_0015
"""
import sqlalchemy as sa
from alembic import op

revision = "20260804_0016"
down_revision = "20260803_0015"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "legacy_supplier_inventory_values",
        sa.Column("constraint_id", sa.String(36), primary_key=True),
        sa.Column("safety_stock", sa.Numeric(20, 6), nullable=False),
        sa.Column("capacity", sa.Numeric(20, 6), nullable=True),
    )
    op.execute(sa.text("INSERT INTO legacy_supplier_inventory_values (constraint_id, safety_stock, capacity) SELECT constraint_id, safety_stock, capacity FROM supplier_ingredient_terms"))
    with op.batch_alter_table("supplier_ingredient_terms") as batch:
        batch.drop_constraint("ck_supplier_term_values", type_="check")
        batch.drop_constraint("ck_supplier_term_capacity", type_="check")
        batch.drop_column("safety_stock")
        batch.drop_column("capacity")
        batch.create_check_constraint("ck_supplier_term_values", "unit_cost >= 0 AND moq >= 0 AND pack_size > 0 AND lead_time_days >= 0")
    op.create_table(
        "inventory_constraints",
        sa.Column("constraint_id", sa.String(36), primary_key=True),
        sa.Column("store_id", sa.String(128), sa.ForeignKey("stores.store_id"), nullable=False),
        sa.Column("ingredient_id", sa.String(36), sa.ForeignKey("ingredients.ingredient_id"), nullable=True),
        sa.Column("constraint_type", sa.String(64), nullable=False),
        sa.Column("value", sa.Numeric(20, 6), nullable=False),
        sa.Column("unit", sa.String(16), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_import_id", sa.String(36), sa.ForeignKey("import_jobs.import_id")),
        sa.Column("source_profile_id", sa.String(36), sa.ForeignKey("import_sheet_profiles.profile_id")),
        sa.Column("source_row_hash", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("store_id", "ingredient_id", "constraint_type", "version", name="uq_inventory_constraint_version"),
        sa.CheckConstraint("value >= 0", name="ck_inventory_constraint_value"),
        sa.CheckConstraint("version >= 1", name="ck_inventory_constraint_version"),
        sa.CheckConstraint("end_date IS NULL OR end_date >= effective_date", name="ck_inventory_constraint_dates"),
    )
    op.create_index("ix_inventory_constraints_lookup", "inventory_constraints", ["store_id", "ingredient_id", "constraint_type", "effective_date"])


def downgrade():
    op.drop_index("ix_inventory_constraints_lookup", table_name="inventory_constraints")
    op.drop_table("inventory_constraints")
    with op.batch_alter_table("supplier_ingredient_terms") as batch:
        batch.drop_constraint("ck_supplier_term_values", type_="check")
        batch.add_column(sa.Column("capacity", sa.Numeric(20, 6), nullable=True))
        batch.add_column(sa.Column("safety_stock", sa.Numeric(20, 6), nullable=False, server_default="0"))
        batch.create_check_constraint("ck_supplier_term_values", "unit_cost >= 0 AND moq >= 0 AND pack_size > 0 AND lead_time_days >= 0 AND safety_stock >= 0")
        batch.create_check_constraint("ck_supplier_term_capacity", "capacity IS NULL OR capacity >= 0")
    op.execute(sa.text("UPDATE supplier_ingredient_terms SET safety_stock = COALESCE((SELECT safety_stock FROM legacy_supplier_inventory_values WHERE legacy_supplier_inventory_values.constraint_id = supplier_ingredient_terms.constraint_id), 0), capacity = (SELECT capacity FROM legacy_supplier_inventory_values WHERE legacy_supplier_inventory_values.constraint_id = supplier_ingredient_terms.constraint_id)"))
    op.drop_table("legacy_supplier_inventory_values")

"""menu addendum

Revision ID: 20260729_0008
Revises: 20260728_0007
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260729_0008"
down_revision: Union[str, None] = "20260728_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("products") as batch:
        batch.add_column(sa.Column("item_type", sa.String(16), nullable=False, server_default="single"))
        batch.add_column(sa.Column("selling_unit", sa.String(16), nullable=True))
        batch.add_column(sa.Column("source_import_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("source_row_hash", sa.String(64), nullable=True))
        batch.create_check_constraint("ck_products_item_type", "item_type IN ('single','combo')")
        batch.create_check_constraint(
            "ck_products_selling_unit",
            "selling_unit IS NULL OR selling_unit IN ('ly','phần','chai','cái','combo')",
        )
        batch.create_foreign_key(
            "fk_products_source_import_id_import_jobs", "import_jobs",
            ["source_import_id"], ["import_id"],
        )
        batch.create_index("ix_products_source_import_id", ["source_import_id"])

    op.create_table(
        "product_bundle_lines",
        sa.Column("bundle_line_id", sa.String(36), nullable=False),
        sa.Column("store_id", sa.String(128), nullable=False),
        sa.Column("combo_product_id", sa.String(36), nullable=False),
        sa.Column("component_product_id", sa.String(36), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_bundle_line_quantity"),
        sa.CheckConstraint("position >= 0", name="ck_bundle_line_position"),
        sa.ForeignKeyConstraint(["store_id"], ["stores.store_id"]),
        sa.ForeignKeyConstraint(["combo_product_id"], ["products.product_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["component_product_id"], ["products.product_id"]),
        sa.PrimaryKeyConstraint("bundle_line_id"),
        sa.UniqueConstraint(
            "combo_product_id", "component_product_id",
            name="uq_bundle_combo_component",
        ),
    )
    op.create_index("ix_bundle_lines_store_id", "product_bundle_lines", ["store_id"])
    op.create_index("ix_bundle_lines_combo_product_id", "product_bundle_lines", ["combo_product_id"])
    op.create_index("ix_bundle_lines_component_product_id", "product_bundle_lines", ["component_product_id"])


def downgrade() -> None:
    op.drop_index("ix_bundle_lines_component_product_id", table_name="product_bundle_lines")
    op.drop_index("ix_bundle_lines_combo_product_id", table_name="product_bundle_lines")
    op.drop_index("ix_bundle_lines_store_id", table_name="product_bundle_lines")
    op.drop_table("product_bundle_lines")
    with op.batch_alter_table("products") as batch:
        batch.drop_index("ix_products_source_import_id")
        batch.drop_constraint("fk_products_source_import_id_import_jobs", type_="foreignkey")
        batch.drop_constraint("ck_products_selling_unit", type_="check")
        batch.drop_constraint("ck_products_item_type", type_="check")
        batch.drop_column("source_row_hash")
        batch.drop_column("source_import_id")
        batch.drop_column("selling_unit")
        batch.drop_column("item_type")

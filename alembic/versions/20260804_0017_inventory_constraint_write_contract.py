"""inventory constraint write contract metadata

Revision ID: 20260804_0017
Revises: 20260804_0016
"""
import sqlalchemy as sa
from alembic import op

revision="20260804_0017"
down_revision="20260804_0016"
branch_labels=None
depends_on=None


def upgrade():
    with op.batch_alter_table("inventory_constraints") as batch:
        batch.add_column(sa.Column("note",sa.String(500),nullable=True))
        batch.add_column(sa.Column("superseded_by_constraint_id",sa.String(36),nullable=True))
        batch.create_foreign_key("fk_inventory_constraint_superseded_by","inventory_constraints",
            ["superseded_by_constraint_id"],["constraint_id"])
    op.execute(sa.text("CREATE UNIQUE INDEX uq_inventory_constraint_scope_version ON inventory_constraints (store_id, COALESCE(ingredient_id, ''), constraint_type, version)"))


def downgrade():
    op.drop_index("uq_inventory_constraint_scope_version",table_name="inventory_constraints")
    with op.batch_alter_table("inventory_constraints") as batch:
        batch.drop_constraint("fk_inventory_constraint_superseded_by",type_="foreignkey")
        batch.drop_column("superseded_by_constraint_id")
        batch.drop_column("note")

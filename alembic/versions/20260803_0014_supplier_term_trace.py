"""supplier term trace

Revision ID: 20260803_0014
Revises: 20260803_0013
"""
import sqlalchemy as sa
from alembic import op
revision="20260803_0014";down_revision="20260803_0013";branch_labels=None;depends_on=None
def upgrade():
    with op.batch_alter_table("procurement_plan_lines") as batch:
        batch.add_column(sa.Column("supplier_term_id",sa.String(36),nullable=True))
        batch.create_foreign_key("fk_plan_line_supplier_term","supplier_ingredient_terms",["supplier_term_id"],["constraint_id"])
def downgrade():
    with op.batch_alter_table("procurement_plan_lines") as batch:
        batch.drop_constraint("fk_plan_line_supplier_term",type_="foreignkey");batch.drop_column("supplier_term_id")

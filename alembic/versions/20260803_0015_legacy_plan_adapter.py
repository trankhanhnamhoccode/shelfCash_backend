"""link legacy plan runs to decision planning

Revision ID: 20260803_0015
Revises: 20260803_0014
"""
import sqlalchemy as sa
from alembic import op

revision="20260803_0015";down_revision="20260803_0014";branch_labels=None;depends_on=None

def upgrade():
    with op.batch_alter_table("plan_runs") as batch:
        batch.add_column(sa.Column("procurement_plan_run_id",sa.String(36),nullable=True))
        batch.add_column(sa.Column("completed_at",sa.DateTime(timezone=True),nullable=True))
        batch.create_foreign_key("fk_plan_runs_procurement_plan_run","procurement_plan_runs",["procurement_plan_run_id"],["procurement_plan_run_id"])

def downgrade():
    with op.batch_alter_table("plan_runs") as batch:
        batch.drop_constraint("fk_plan_runs_procurement_plan_run",type_="foreignkey")
        batch.drop_column("completed_at");batch.drop_column("procurement_plan_run_id")

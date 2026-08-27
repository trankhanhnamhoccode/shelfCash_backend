"""canonical ingredient expiry tracking semantics

Revision ID: 20260827_0025
Revises: 20260826_0024
"""
from alembic import op
import sqlalchemy as sa

revision = "20260827_0025"
down_revision = "20260826_0024"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("ingredients") as batch:
        batch.add_column(sa.Column("expiry_tracking_mode", sa.String(length=16), nullable=False, server_default="unknown"))
        batch.add_column(sa.Column("expiry_tracking_source", sa.String(length=16), nullable=False, server_default="inferred"))
        batch.create_check_constraint("ck_ingredients_expiry_tracking_mode", "expiry_tracking_mode IN ('required','not_required','unknown')")
        batch.create_check_constraint("ck_ingredients_expiry_tracking_source", "expiry_tracking_source IN ('declared','inferred')")


def downgrade():
    with op.batch_alter_table("ingredients") as batch:
        batch.drop_constraint("ck_ingredients_expiry_tracking_source", type_="check")
        batch.drop_constraint("ck_ingredients_expiry_tracking_mode", type_="check")
        batch.drop_column("expiry_tracking_source")
        batch.drop_column("expiry_tracking_mode")

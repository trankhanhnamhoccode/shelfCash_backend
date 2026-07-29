"""supplier packaging unit

Revision ID: 20260729_0009
Revises: 20260729_0008
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260729_0009"
down_revision: Union[str, None] = "20260729_0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("supplier_ingredient_terms") as batch:
        batch.add_column(sa.Column("order_unit", sa.String(64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("supplier_ingredient_terms") as batch:
        batch.drop_column("order_unit")

"""use SKU, not product name, as variant identity

Revision ID: 20260731_0010
Revises: 20260729_0009
"""
from typing import Sequence, Union

from alembic import op

revision: str = "20260731_0010"
down_revision: Union[str, None] = "20260729_0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("products") as batch:
        batch.drop_constraint("uq_products_store_name", type_="unique")
        batch.create_index("ix_products_store_normalized_name", ["store_id", "normalized_name"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("products") as batch:
        batch.drop_index("ix_products_store_normalized_name")
        batch.create_unique_constraint("uq_products_store_name", ["store_id", "normalized_name"])

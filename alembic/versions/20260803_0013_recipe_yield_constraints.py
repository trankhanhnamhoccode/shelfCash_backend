"""recipe yield constraints

Revision ID: 20260803_0013
Revises: 20260803_0012
"""
from typing import Sequence,Union
from alembic import op

revision="20260803_0013";down_revision="20260803_0012"
branch_labels:Union[str,Sequence[str],None]=None;depends_on:Union[str,Sequence[str],None]=None

def upgrade():
    with op.batch_alter_table("recipe_versions") as batch:
        batch.create_check_constraint("ck_recipe_yield_positive","yield_quantity > 0")
        batch.create_check_constraint("ck_recipe_loss_rate","process_loss_rate >= 0 AND process_loss_rate < 1")

def downgrade():
    with op.batch_alter_table("recipe_versions") as batch:
        batch.drop_constraint("ck_recipe_loss_rate",type_="check")
        batch.drop_constraint("ck_recipe_yield_positive",type_="check")

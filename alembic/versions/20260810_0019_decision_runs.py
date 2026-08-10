"""persist unified Decision Packages

Revision ID: 20260810_0019
Revises: 20260804_0018
"""
import sqlalchemy as sa
from alembic import op

revision = "20260810_0019"
down_revision = "20260804_0018"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "decision_runs",
        sa.Column("decision_run_id", sa.String(36), primary_key=True),
        sa.Column("store_id", sa.String(128), sa.ForeignKey("stores.store_id"), nullable=False),
        sa.Column("forecast_run_id", sa.String(36), sa.ForeignKey("forecast_runs.forecast_run_id"), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("horizon_days", sa.Integer(), nullable=False),
        sa.Column("engine_mode", sa.String(24), nullable=False),
        sa.Column("status", sa.String(64), nullable=False),
        sa.Column("scenario_method", sa.String(64), nullable=False),
        sa.Column("scenario_count", sa.Integer(), nullable=False),
        sa.Column("random_seed", sa.Integer(), nullable=False),
        sa.Column("recommended_strategy", sa.String(24)),
        sa.Column("request_json", sa.Text(), nullable=False),
        sa.Column("package_json", sa.Text(), nullable=False),
        sa.Column("warnings_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_decision_runs_store_id", "decision_runs", ["store_id"])
    op.create_index("ix_decision_runs_forecast_run_id", "decision_runs", ["forecast_run_id"])


def downgrade():
    op.drop_index("ix_decision_runs_forecast_run_id", table_name="decision_runs")
    op.drop_index("ix_decision_runs_store_id", table_name="decision_runs")
    op.drop_table("decision_runs")

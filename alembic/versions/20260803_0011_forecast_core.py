"""forecast core persistence

Revision ID: 20260803_0011
Revises: 20260731_0010
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260803_0011"
down_revision: Union[str, None] = "20260731_0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("sales_daily") as batch:
        batch.add_column(sa.Column("is_stockout", sa.Boolean(), nullable=True))
    with op.batch_alter_table("forecast_runs") as batch:
        batch.add_column(sa.Column("warnings_json", sa.Text(), nullable=True))
        batch.add_column(sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "forecast_model_versions",
        sa.Column("model_version_id", sa.String(36), primary_key=True),
        sa.Column("store_id", sa.String(128), sa.ForeignKey("stores.store_id"), nullable=False),
        sa.Column("model_version", sa.String(128), nullable=False),
        sa.Column("artifact_key", sa.String(512), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("trained_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("history_start", sa.Date(), nullable=True),
        sa.Column("history_end", sa.Date(), nullable=True),
        sa.Column("metrics_json", sa.Text(), nullable=True),
        sa.Column("warnings_json", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("store_id", "model_version", name="uq_forecast_model_store_version"),
    )
    op.create_index("ix_forecast_model_versions_store_id", "forecast_model_versions", ["store_id"])
    op.create_index("ix_forecast_model_active", "forecast_model_versions", ["store_id"], unique=True,
                    sqlite_where=sa.text("is_active = 1"), postgresql_where=sa.text("is_active = true"))
    op.create_table(
        "forecast_predictions",
        sa.Column("prediction_id", sa.String(36), primary_key=True),
        sa.Column("forecast_run_id", sa.String(36), sa.ForeignKey("forecast_runs.forecast_run_id", ondelete="CASCADE"), nullable=False),
        sa.Column("store_id", sa.String(128), sa.ForeignKey("stores.store_id"), nullable=False),
        sa.Column("product_id", sa.String(36), sa.ForeignKey("products.product_id"), nullable=False),
        sa.Column("product_name", sa.String(255), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=False),
        sa.Column("horizon", sa.Integer(), nullable=False),
        sa.Column("p25", sa.Numeric(20, 6), nullable=False),
        sa.Column("p50", sa.Numeric(20, 6), nullable=False),
        sa.Column("p75", sa.Numeric(20, 6), nullable=False),
        sa.Column("interval_lower", sa.Numeric(20, 6), nullable=False),
        sa.Column("interval_upper", sa.Numeric(20, 6), nullable=False),
        sa.Column("baseline_p50", sa.Numeric(20, 6), nullable=False),
        sa.Column("calibration_source", sa.String(64), nullable=False),
        sa.Column("warnings_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("forecast_run_id", "product_id", "target_date", "horizon", name="uq_forecast_prediction_run_target"),
    )
    op.create_index("ix_forecast_predictions_forecast_run_id", "forecast_predictions", ["forecast_run_id"])
    op.create_index("ix_forecast_predictions_store_id", "forecast_predictions", ["store_id"])


def downgrade() -> None:
    op.drop_table("forecast_predictions")
    op.drop_index("ix_forecast_model_active", table_name="forecast_model_versions")
    op.drop_table("forecast_model_versions")
    with op.batch_alter_table("forecast_runs") as batch:
        batch.drop_column("completed_at")
        batch.drop_column("warnings_json")
    with op.batch_alter_table("sales_daily") as batch:
        batch.drop_column("is_stockout")

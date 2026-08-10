"""persist realized forecast residuals for stochastic decision runs

Revision ID: 20260810_0020
Revises: 20260810_0019
"""
import sqlalchemy as sa
from alembic import op

revision = "20260810_0020"
down_revision = "20260810_0019"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("forecast_residuals",
        sa.Column("residual_id", sa.String(36), primary_key=True),
        sa.Column("store_id", sa.String(128), sa.ForeignKey("stores.store_id"), nullable=False),
        sa.Column("forecast_run_id", sa.String(36), sa.ForeignKey("forecast_runs.forecast_run_id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", sa.String(36), sa.ForeignKey("products.product_id"), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=False), sa.Column("horizon", sa.Integer(), nullable=False),
        sa.Column("actual_value", sa.Numeric(20, 6), nullable=False), sa.Column("predicted_p25", sa.Numeric(20, 6), nullable=False),
        sa.Column("predicted_p50", sa.Numeric(20, 6), nullable=False), sa.Column("predicted_p75", sa.Numeric(20, 6), nullable=False),
        sa.Column("residual", sa.Numeric(20, 6), nullable=False), sa.Column("forecast_origin", sa.Date(), nullable=False),
        sa.Column("model_version", sa.String(128)), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("forecast_run_id", "product_id", "target_date", "horizon", name="uq_forecast_residual_target"),
    )
    op.create_index("ix_forecast_residuals_store_id", "forecast_residuals", ["store_id"])
    op.create_index("ix_forecast_residuals_forecast_run_id", "forecast_residuals", ["forecast_run_id"])
    op.create_index("ix_forecast_residuals_product_id", "forecast_residuals", ["product_id"])

def downgrade():
    op.drop_index("ix_forecast_residuals_product_id", table_name="forecast_residuals")
    op.drop_index("ix_forecast_residuals_forecast_run_id", table_name="forecast_residuals")
    op.drop_index("ix_forecast_residuals_store_id", table_name="forecast_residuals")
    op.drop_table("forecast_residuals")

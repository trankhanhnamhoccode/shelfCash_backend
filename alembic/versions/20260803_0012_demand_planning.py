"""ingredient demand and procurement planning

Revision ID: 20260803_0012
Revises: 20260803_0011
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "20260803_0012"
down_revision: Union[str, None] = "20260803_0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("recipe_versions") as batch:
        batch.add_column(sa.Column("yield_quantity", sa.Numeric(20, 6), nullable=False, server_default="1"))
        batch.add_column(sa.Column("process_loss_rate", sa.Numeric(10, 6), nullable=False, server_default="0"))
    op.create_table("ingredient_demand_runs",
        sa.Column("ingredient_demand_run_id",sa.String(36),primary_key=True),
        sa.Column("forecast_run_id",sa.String(36),sa.ForeignKey("forecast_runs.forecast_run_id",ondelete="CASCADE"),nullable=False),
        sa.Column("store_id",sa.String(128),sa.ForeignKey("stores.store_id"),nullable=False),
        sa.Column("status",sa.String(24),nullable=False),sa.Column("warnings_json",sa.Text(),nullable=False),
        sa.Column("failure_code",sa.String(64)),sa.Column("failure_message",sa.String(500)),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),sa.Column("completed_at",sa.DateTime(timezone=True)),
        sa.UniqueConstraint("forecast_run_id",name="uq_ingredient_demand_forecast_run"))
    op.create_index("ix_ingredient_demand_runs_forecast_run_id","ingredient_demand_runs",["forecast_run_id"])
    op.create_index("ix_ingredient_demand_runs_store_id","ingredient_demand_runs",["store_id"])
    op.create_table("ingredient_demand_predictions",
        sa.Column("ingredient_demand_prediction_id",sa.String(36),primary_key=True),
        sa.Column("ingredient_demand_run_id",sa.String(36),sa.ForeignKey("ingredient_demand_runs.ingredient_demand_run_id",ondelete="CASCADE"),nullable=False),
        sa.Column("forecast_run_id",sa.String(36),sa.ForeignKey("forecast_runs.forecast_run_id",ondelete="CASCADE"),nullable=False),
        sa.Column("store_id",sa.String(128),sa.ForeignKey("stores.store_id"),nullable=False),
        sa.Column("ingredient_id",sa.String(36),sa.ForeignKey("ingredients.ingredient_id"),nullable=False),
        sa.Column("ingredient_name",sa.String(255),nullable=False),sa.Column("target_date",sa.Date(),nullable=False),
        sa.Column("horizon",sa.Integer(),nullable=False),sa.Column("unit",sa.String(16),nullable=False),
        sa.Column("p25",sa.Numeric(24,8),nullable=False),sa.Column("p50",sa.Numeric(24,8),nullable=False),sa.Column("p75",sa.Numeric(24,8),nullable=False),
        sa.Column("source_product_count",sa.Integer(),nullable=False),sa.Column("contributions_json",sa.Text(),nullable=False),
        sa.Column("warnings_json",sa.Text(),nullable=False),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),
        sa.UniqueConstraint("ingredient_demand_run_id","ingredient_id","target_date",name="uq_ingredient_demand_target"))
    op.create_index("ix_ingredient_demand_predictions_ingredient_demand_run_id","ingredient_demand_predictions",["ingredient_demand_run_id"])
    op.create_table("procurement_plan_runs",
        sa.Column("procurement_plan_run_id",sa.String(36),primary_key=True),sa.Column("forecast_run_id",sa.String(36),sa.ForeignKey("forecast_runs.forecast_run_id",ondelete="CASCADE"),nullable=False),
        sa.Column("ingredient_demand_run_id",sa.String(36),sa.ForeignKey("ingredient_demand_runs.ingredient_demand_run_id"),nullable=False),
        sa.Column("store_id",sa.String(128),sa.ForeignKey("stores.store_id"),nullable=False),sa.Column("status",sa.String(24),nullable=False),
        sa.Column("request_json",sa.Text(),nullable=False),sa.Column("recommended_strategy",sa.String(24)),sa.Column("warnings_json",sa.Text(),nullable=False),
        sa.Column("failure_code",sa.String(64)),sa.Column("failure_message",sa.String(500)),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),sa.Column("completed_at",sa.DateTime(timezone=True)))
    op.create_index("ix_procurement_plan_runs_forecast_run_id","procurement_plan_runs",["forecast_run_id"]);op.create_index("ix_procurement_plan_runs_store_id","procurement_plan_runs",["store_id"])
    op.create_table("procurement_plans",
        sa.Column("procurement_plan_id",sa.String(36),primary_key=True),sa.Column("procurement_plan_run_id",sa.String(36),sa.ForeignKey("procurement_plan_runs.procurement_plan_run_id",ondelete="CASCADE"),nullable=False),
        sa.Column("strategy",sa.String(24),nullable=False),sa.Column("is_feasible",sa.Boolean(),nullable=False),sa.Column("is_recommended",sa.Boolean(),nullable=False),
        sa.Column("total_purchase_cost",sa.Integer(),nullable=False),sa.Column("projected_shortage_quantity",sa.Numeric(24,8),nullable=False),sa.Column("projected_waste_quantity",sa.Numeric(24,8),nullable=False),
        sa.Column("fill_rate",sa.Numeric(12,8),nullable=False),sa.Column("budget_used",sa.Integer(),nullable=False),sa.Column("metrics_json",sa.Text(),nullable=False),sa.Column("daily_projections_json",sa.Text(),nullable=False),
        sa.Column("warnings_json",sa.Text(),nullable=False),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),sa.UniqueConstraint("procurement_plan_run_id","strategy",name="uq_procurement_plan_strategy"))
    op.create_index("ix_procurement_plans_procurement_plan_run_id","procurement_plans",["procurement_plan_run_id"])
    op.create_table("procurement_plan_lines",
        sa.Column("procurement_plan_line_id",sa.String(36),primary_key=True),sa.Column("procurement_plan_id",sa.String(36),sa.ForeignKey("procurement_plans.procurement_plan_id",ondelete="CASCADE"),nullable=False),
        sa.Column("ingredient_id",sa.String(36),sa.ForeignKey("ingredients.ingredient_id"),nullable=False),sa.Column("supplier_id",sa.String(36),sa.ForeignKey("suppliers.supplier_id")),
        sa.Column("order_date",sa.Date(),nullable=False),sa.Column("expected_arrival_date",sa.Date()),sa.Column("raw_required_quantity",sa.Numeric(24,8),nullable=False),sa.Column("order_quantity",sa.Numeric(24,8),nullable=False),
        sa.Column("unit",sa.String(16),nullable=False),sa.Column("pack_count",sa.Integer()),sa.Column("unit_cost",sa.Integer()),sa.Column("line_cost",sa.Integer(),nullable=False),sa.Column("moq",sa.Numeric(24,8)),sa.Column("pack_size",sa.Numeric(24,8)),sa.Column("lead_time_days",sa.Integer()),
        sa.Column("reason_codes_json",sa.Text(),nullable=False),sa.Column("warnings_json",sa.Text(),nullable=False),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),sa.UniqueConstraint("procurement_plan_id","ingredient_id",name="uq_procurement_plan_ingredient"))
    op.create_index("ix_procurement_plan_lines_procurement_plan_id","procurement_plan_lines",["procurement_plan_id"])


def downgrade() -> None:
    op.drop_table("procurement_plan_lines");op.drop_table("procurement_plans");op.drop_table("procurement_plan_runs")
    op.drop_table("ingredient_demand_predictions");op.drop_table("ingredient_demand_runs")
    with op.batch_alter_table("recipe_versions") as batch:
        batch.drop_column("process_loss_rate");batch.drop_column("yield_quantity")

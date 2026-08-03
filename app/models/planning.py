from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.store import utc_now


class IngredientDemandRunModel(Base):
    __tablename__ = "ingredient_demand_runs"
    __table_args__ = (UniqueConstraint("forecast_run_id", name="uq_ingredient_demand_forecast_run"),)
    ingredient_demand_run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    forecast_run_id: Mapped[str] = mapped_column(ForeignKey("forecast_runs.forecast_run_id", ondelete="CASCADE"), nullable=False, index=True)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.store_id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    warnings_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    failure_code: Mapped[str | None] = mapped_column(String(64))
    failure_message: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IngredientDemandPredictionModel(Base):
    __tablename__ = "ingredient_demand_predictions"
    __table_args__ = (UniqueConstraint("ingredient_demand_run_id", "ingredient_id", "target_date", name="uq_ingredient_demand_target"),)
    ingredient_demand_prediction_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    ingredient_demand_run_id: Mapped[str] = mapped_column(ForeignKey("ingredient_demand_runs.ingredient_demand_run_id", ondelete="CASCADE"), nullable=False, index=True)
    forecast_run_id: Mapped[str] = mapped_column(ForeignKey("forecast_runs.forecast_run_id", ondelete="CASCADE"), nullable=False)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.store_id"), nullable=False)
    ingredient_id: Mapped[str] = mapped_column(ForeignKey("ingredients.ingredient_id"), nullable=False)
    ingredient_name: Mapped[str] = mapped_column(String(255), nullable=False)
    target_date: Mapped[date] = mapped_column(Date, nullable=False)
    horizon: Mapped[int] = mapped_column(Integer, nullable=False)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    p25: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    p50: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    p75: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    source_product_count: Mapped[int] = mapped_column(Integer, nullable=False)
    contributions_json: Mapped[str] = mapped_column(Text, nullable=False)
    warnings_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class ProcurementPlanRunModel(Base):
    __tablename__ = "procurement_plan_runs"
    procurement_plan_run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    forecast_run_id: Mapped[str] = mapped_column(ForeignKey("forecast_runs.forecast_run_id", ondelete="CASCADE"), nullable=False, index=True)
    ingredient_demand_run_id: Mapped[str] = mapped_column(ForeignKey("ingredient_demand_runs.ingredient_demand_run_id"), nullable=False)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.store_id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    request_json: Mapped[str] = mapped_column(Text, nullable=False)
    recommended_strategy: Mapped[str | None] = mapped_column(String(24))
    warnings_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    failure_code: Mapped[str | None] = mapped_column(String(64))
    failure_message: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProcurementPlanModel(Base):
    __tablename__ = "procurement_plans"
    __table_args__ = (UniqueConstraint("procurement_plan_run_id", "strategy", name="uq_procurement_plan_strategy"),)
    procurement_plan_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    procurement_plan_run_id: Mapped[str] = mapped_column(ForeignKey("procurement_plan_runs.procurement_plan_run_id", ondelete="CASCADE"), nullable=False, index=True)
    strategy: Mapped[str] = mapped_column(String(24), nullable=False)
    is_feasible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    is_recommended: Mapped[bool] = mapped_column(Boolean, nullable=False)
    total_purchase_cost: Mapped[int] = mapped_column(Integer, nullable=False)
    projected_shortage_quantity: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    projected_waste_quantity: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    fill_rate: Mapped[Decimal] = mapped_column(Numeric(12, 8), nullable=False)
    budget_used: Mapped[int] = mapped_column(Integer, nullable=False)
    metrics_json: Mapped[str] = mapped_column(Text, nullable=False)
    daily_projections_json: Mapped[str] = mapped_column(Text, nullable=False)
    warnings_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class ProcurementPlanLineModel(Base):
    __tablename__ = "procurement_plan_lines"
    __table_args__ = (UniqueConstraint("procurement_plan_id", "ingredient_id", name="uq_procurement_plan_ingredient"),)
    procurement_plan_line_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    procurement_plan_id: Mapped[str] = mapped_column(ForeignKey("procurement_plans.procurement_plan_id", ondelete="CASCADE"), nullable=False, index=True)
    ingredient_id: Mapped[str] = mapped_column(ForeignKey("ingredients.ingredient_id"), nullable=False)
    supplier_id: Mapped[str | None] = mapped_column(ForeignKey("suppliers.supplier_id"))
    supplier_term_id: Mapped[str | None] = mapped_column(ForeignKey("supplier_ingredient_terms.constraint_id"))
    order_date: Mapped[date] = mapped_column(Date, nullable=False)
    expected_arrival_date: Mapped[date | None] = mapped_column(Date)
    raw_required_quantity: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    order_quantity: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    pack_count: Mapped[int | None] = mapped_column(Integer)
    unit_cost: Mapped[int | None] = mapped_column(Integer)
    line_cost: Mapped[int] = mapped_column(Integer, nullable=False)
    moq: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    pack_size: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    lead_time_days: Mapped[int | None] = mapped_column(Integer)
    reason_codes_json: Mapped[str] = mapped_column(Text, nullable=False)
    warnings_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)

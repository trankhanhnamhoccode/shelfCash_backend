from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, Index, text
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base
from app.models.store import utc_now

class BudgetPeriodModel(Base):
    __tablename__="budget_periods"; __table_args__=(UniqueConstraint("store_id","period",name="uq_budget_store_period"),)
    budget_period_id:Mapped[str]=mapped_column(String(36),primary_key=True);store_id:Mapped[str]=mapped_column(ForeignKey("stores.store_id"),index=True)
    period:Mapped[str]=mapped_column(String(7));monthly_budget:Mapped[int]=mapped_column(Integer,default=0);reserved_budget:Mapped[int]=mapped_column(Integer,default=0);spent_budget:Mapped[int]=mapped_column(Integer,default=0)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utc_now);updated_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utc_now,onupdate=utc_now)

class ForecastRunModel(Base):
    __tablename__="forecast_runs"
    forecast_run_id:Mapped[str]=mapped_column(String(36),primary_key=True);store_id:Mapped[str]=mapped_column(ForeignKey("stores.store_id"),index=True)
    cutoff_date:Mapped[date]=mapped_column(Date);horizon_days:Mapped[int]=mapped_column(Integer);quantiles_json:Mapped[str]=mapped_column(Text);scope_json:Mapped[str]=mapped_column(Text)
    use_latest_calendar:Mapped[bool]=mapped_column(Boolean,default=True);status:Mapped[str]=mapped_column(String(24));engine_status:Mapped[str]=mapped_column(String(40));request_hash:Mapped[str]=mapped_column(String(64))
    model_version:Mapped[str|None]=mapped_column(String(128));calibrator_version:Mapped[str|None]=mapped_column(String(128));input_snapshot_json:Mapped[str|None]=mapped_column(Text);warnings_json:Mapped[str|None]=mapped_column(Text);failure_code:Mapped[str|None]=mapped_column(String(64));failure_message:Mapped[str|None]=mapped_column(String(500));created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utc_now);completed_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True))


class ForecastModelVersionModel(Base):
    __tablename__ = "forecast_model_versions"
    __table_args__ = (
        UniqueConstraint("store_id", "model_version", name="uq_forecast_model_store_version"),
        Index("ix_forecast_model_active", "store_id", unique=True,
              sqlite_where=text("is_active = 1"), postgresql_where=text("is_active = true")),
    )
    model_version_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.store_id"), nullable=False, index=True)
    model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    artifact_key: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    trained_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    history_start: Mapped[date | None] = mapped_column(Date)
    history_end: Mapped[date | None] = mapped_column(Date)
    metrics_json: Mapped[str | None] = mapped_column(Text)
    warnings_json: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class ForecastPredictionModel(Base):
    __tablename__ = "forecast_predictions"
    __table_args__ = (
        UniqueConstraint("forecast_run_id", "product_id", "target_date", "horizon", name="uq_forecast_prediction_run_target"),
    )
    prediction_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    forecast_run_id: Mapped[str] = mapped_column(ForeignKey("forecast_runs.forecast_run_id", ondelete="CASCADE"), nullable=False, index=True)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.store_id"), nullable=False, index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.product_id"), nullable=False)
    product_name: Mapped[str] = mapped_column(String(255), nullable=False)
    target_date: Mapped[date] = mapped_column(Date, nullable=False)
    horizon: Mapped[int] = mapped_column(Integer, nullable=False)
    p25: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    p50: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    p75: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    interval_lower: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    interval_upper: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    baseline_p50: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    calibration_source: Mapped[str] = mapped_column(String(64), nullable=False)
    warnings_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

class ForecastResidualModel(Base):
    """Only realized, out-of-sample forecast errors are eligible for SAA."""
    __tablename__ = "forecast_residuals"
    __table_args__ = (UniqueConstraint("forecast_run_id", "product_id", "target_date", "horizon", name="uq_forecast_residual_target"),)
    residual_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.store_id"), nullable=False, index=True)
    forecast_run_id: Mapped[str] = mapped_column(ForeignKey("forecast_runs.forecast_run_id", ondelete="CASCADE"), nullable=False, index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.product_id"), nullable=False, index=True)
    target_date: Mapped[date] = mapped_column(Date, nullable=False)
    horizon: Mapped[int] = mapped_column(Integer, nullable=False)
    actual_value: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    predicted_p25: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    predicted_p50: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    predicted_p75: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    residual: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    forecast_origin: Mapped[date] = mapped_column(Date, nullable=False)
    model_version: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

class PlanRunModel(Base):
    __tablename__="plan_runs"
    plan_run_id:Mapped[str]=mapped_column(String(36),primary_key=True);store_id:Mapped[str]=mapped_column(ForeignKey("stores.store_id"),index=True);forecast_run_id:Mapped[str]=mapped_column(ForeignKey("forecast_runs.forecast_run_id"))
    strategy:Mapped[str]=mapped_column(String(16));budget_limit:Mapped[int]=mapped_column(Integer);as_of_date:Mapped[date]=mapped_column(Date);include_open_purchase_orders:Mapped[bool]=mapped_column(Boolean)
    status:Mapped[str]=mapped_column(String(24));engine_status:Mapped[str]=mapped_column(String(40));request_hash:Mapped[str]=mapped_column(String(64));input_snapshot_json:Mapped[str|None]=mapped_column(Text);warnings_json:Mapped[str]=mapped_column(Text,default="[]");failure_code:Mapped[str|None]=mapped_column(String(64));failure_message:Mapped[str|None]=mapped_column(String(500));procurement_plan_run_id:Mapped[str|None]=mapped_column(ForeignKey("procurement_plan_runs.procurement_plan_run_id"));created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utc_now);completed_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True))

class RecommendationModel(Base):
    __tablename__="recommendations"
    recommendation_id:Mapped[str]=mapped_column(String(36),primary_key=True);plan_run_id:Mapped[str]=mapped_column(ForeignKey("plan_runs.plan_run_id"),index=True);store_id:Mapped[str]=mapped_column(ForeignKey("stores.store_id"),index=True);ingredient_id:Mapped[str]=mapped_column(ForeignKey("ingredients.ingredient_id"));unit:Mapped[str]=mapped_column(String(16));order_quantity:Mapped[Decimal]=mapped_column(Numeric(20,6));unit_cost:Mapped[int]=mapped_column(Integer);cost:Mapped[int]=mapped_column(Integer);supplier_id:Mapped[str]=mapped_column(ForeignKey("suppliers.supplier_id"));moq:Mapped[Decimal]=mapped_column(Numeric(20,6));pack_size:Mapped[Decimal]=mapped_column(Numeric(20,6));lead_time_days:Mapped[int]=mapped_column(Integer);created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utc_now)

class PurchaseOrderModel(Base):
    __tablename__="purchase_orders"
    po_id:Mapped[str]=mapped_column(String(36),primary_key=True);store_id:Mapped[str]=mapped_column(ForeignKey("stores.store_id"),index=True);plan_run_id:Mapped[str]=mapped_column(ForeignKey("plan_runs.plan_run_id"));supplier_id:Mapped[str]=mapped_column(ForeignKey("suppliers.supplier_id"))
    order_date:Mapped[date]=mapped_column(Date);delivery_date:Mapped[date]=mapped_column(Date);strategy:Mapped[str]=mapped_column(String(16));status:Mapped[str]=mapped_column(String(24),default="draft");total:Mapped[int]=mapped_column(Integer);budget_after:Mapped[int]=mapped_column(Integer);version:Mapped[int]=mapped_column(Integer,default=1);confirmed_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True));received_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True));created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utc_now);updated_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utc_now,onupdate=utc_now)

class PurchaseOrderLineModel(Base):
    __tablename__="purchase_order_lines"
    po_line_id:Mapped[str]=mapped_column(String(36),primary_key=True);po_id:Mapped[str]=mapped_column(ForeignKey("purchase_orders.po_id"),index=True);recommendation_id:Mapped[str|None]=mapped_column(String(36));ingredient_id:Mapped[str]=mapped_column(ForeignKey("ingredients.ingredient_id"));ordered_quantity:Mapped[Decimal]=mapped_column(Numeric(20,6));received_quantity:Mapped[Decimal]=mapped_column(Numeric(20,6),default=0);unit:Mapped[str]=mapped_column(String(16));unit_cost:Mapped[int]=mapped_column(Integer);cost:Mapped[int]=mapped_column(Integer);moq:Mapped[Decimal]=mapped_column(Numeric(20,6));pack_size:Mapped[Decimal]=mapped_column(Numeric(20,6));shelf_life_days:Mapped[int|None]=mapped_column(Integer);version:Mapped[int]=mapped_column(Integer,default=1);created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utc_now);updated_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utc_now,onupdate=utc_now)

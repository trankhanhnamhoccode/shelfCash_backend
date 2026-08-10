from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.store import utc_now


class DecisionRunModel(Base):
    """Persisted, replayable Decision Package; trajectories remain in core only."""

    __tablename__ = "decision_runs"
    decision_run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.store_id"), nullable=False, index=True)
    forecast_run_id: Mapped[str] = mapped_column(ForeignKey("forecast_runs.forecast_run_id"), nullable=False, index=True)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False)
    engine_mode: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    scenario_method: Mapped[str] = mapped_column(String(64), nullable=False)
    scenario_count: Mapped[int] = mapped_column(Integer, nullable=False)
    random_seed: Mapped[int] = mapped_column(Integer, nullable=False)
    recommended_strategy: Mapped[str | None] = mapped_column(String(24))
    request_json: Mapped[str] = mapped_column(Text, nullable=False)
    package_json: Mapped[str] = mapped_column(Text, nullable=False)
    warnings_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

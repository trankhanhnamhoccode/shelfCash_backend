import json
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import delete

from app.core.exceptions import PlanningError
from app.core.provenance import canonical_hash
from app.models.planning import IngredientDemandPredictionModel, IngredientDemandRunModel
from app.repositories.audit_logs import AuditLogRepository
from app.repositories.idempotency import IdempotencyRepository
from app.repositories.planning import PlanningRepository
from app.services.audit_service import AuditService
from app.services.decision.adapters.bom_adapter import CoreBomAdapter
from app.services.idempotency_service import IdempotencyService


def now():
    return datetime.now(timezone.utc)


def dump(value):
    return json.dumps(value, ensure_ascii=False, default=str)


class IngredientDemandLifecycle:
    """Owns the current Ingredient Demand application command and read lifecycle."""

    def __init__(self, session_factory, completed_forecast_reader):
        self.session_factory = session_factory
        self.completed_forecast_reader = completed_forecast_reader

    def generate(self, store_id: str, forecast_run_id: str, key: str | None = None, refresh: bool = False):
        endpoint = f"/api/v1/stores/{store_id}/forecast-runs/{forecast_run_id}/ingredient-demand"
        request_hash = canonical_hash({"forecast_run_id": forecast_run_id})
        with self.session_factory() as session:
            forecast, predictions = self.completed_forecast_reader.load(session, store_id, forecast_run_id)
            repository = PlanningRepository(session)
            existing = repository.demand_run_for_forecast(forecast_run_id)
            if key:
                replay = IdempotencyService(IdempotencyRepository(session)).register(
                    store_id=store_id,
                    endpoint=endpoint,
                    http_method="POST",
                    idempotency_key=key,
                    request_hash=request_hash,
                )
                if replay.is_replay:
                    session.rollback()
                    return self.read(store_id, forecast_run_id)
            if existing and existing.status == "completed" and not refresh:
                return self.read(store_id, forecast_run_id)
            demand_run = existing or IngredientDemandRunModel(
                ingredient_demand_run_id=str(uuid4()),
                forecast_run_id=forecast_run_id,
                store_id=store_id,
                status="running",
                warnings_json="[]",
                created_at=now(),
            )
            if not existing:
                session.add(demand_run)
                session.flush()
            if existing and refresh:
                session.execute(delete(IngredientDemandPredictionModel).where(
                    IngredientDemandPredictionModel.ingredient_demand_run_id == demand_run.ingredient_demand_run_id,
                ))
                demand_run.status = "running"
                demand_run.failure_code = None
                demand_run.failure_message = None
                demand_run.completed_at = None
            try:
                scope = json.loads(forecast.scope_json or "{}").get("ingredient_ids", [])
                rows = CoreBomAdapter(session).expand(store_id, forecast, predictions, scope)
                warnings = sorted({warning for row in rows for warning in row["warnings"]})
                if scope and not rows:
                    warnings.append("INGREDIENT_SCOPE_NO_MATCH")
                for row in rows:
                    session.add(IngredientDemandPredictionModel(
                        ingredient_demand_prediction_id=str(uuid4()),
                        ingredient_demand_run_id=demand_run.ingredient_demand_run_id,
                        forecast_run_id=forecast_run_id,
                        store_id=store_id,
                        ingredient_id=row["ingredient_id"],
                        ingredient_name=row["ingredient_name"],
                        target_date=row["target_date"],
                        horizon=row["horizon"],
                        unit=row["unit"],
                        p25=row["p25"],
                        p50=row["p50"],
                        p75=row["p75"],
                        source_product_count=row["source_product_count"],
                        contributions_json=dump(row["contributions"]),
                        warnings_json=dump(row["warnings"]),
                        created_at=now(),
                    ))
                demand_run.status = "completed"
                demand_run.completed_at = now()
                demand_run.warnings_json = dump(warnings)
                AuditService(AuditLogRepository(session)).record(
                    store_id=store_id,
                    action="ingredient_demand_generated",
                    resource_type="ingredient_demand_run",
                    resource_id=demand_run.ingredient_demand_run_id,
                    after={"forecast_run_id": forecast_run_id, "prediction_count": len(rows)},
                    source="planning_service",
                )
                if key:
                    record = IdempotencyRepository(session).get(
                        store_id=store_id,
                        endpoint=endpoint,
                        http_method="POST",
                        idempotency_key=key,
                    )
                    record.resource_type = "ingredient_demand_run"
                    record.resource_id = demand_run.ingredient_demand_run_id
                    record.response_status = 200
                session.commit()
            except PlanningError as exc:
                demand_run.status = "blocked"
                demand_run.failure_code = exc.code
                demand_run.failure_message = exc.message
                demand_run.completed_at = now()
                session.commit()
                raise
        return self.read(store_id, forecast_run_id)

    def read(self, store_id: str, forecast_run_id: str):
        with self.session_factory() as session:
            self.completed_forecast_reader.load(session, store_id, forecast_run_id)
            repository = PlanningRepository(session)
            demand_run = repository.demand_run_for_forecast(forecast_run_id)
            if not demand_run:
                raise PlanningError("INGREDIENT_DEMAND_INCOMPLETE", "Ingredient demand chưa được tạo.", http_status=404)
            rows = repository.demand_predictions(demand_run.ingredient_demand_run_id)
            warnings = json.loads(demand_run.warnings_json or "[]")
            if demand_run.status == "completed" and not rows and "INGREDIENT_SCOPE_NO_MATCH" not in warnings:
                raise PlanningError("INGREDIENT_DEMAND_INCOMPLETE", "Completed ingredient demand thiếu predictions.", http_status=500)
            return {
                "ingredient_demand_run_id": demand_run.ingredient_demand_run_id,
                "forecast_run_id": forecast_run_id,
                "store_id": store_id,
                "status": demand_run.status,
                "warnings": warnings,
                "failure_code": demand_run.failure_code,
                "failure_message": demand_run.failure_message,
                "created_at": demand_run.created_at,
                "completed_at": demand_run.completed_at,
                "predictions": [{
                    "ingredient_id": row.ingredient_id,
                    "ingredient_name": row.ingredient_name,
                    "target_date": row.target_date,
                    "horizon": row.horizon,
                    "unit": row.unit,
                    "p25": float(row.p25),
                    "p50": float(row.p50),
                    "p75": float(row.p75),
                    "source_product_count": row.source_product_count,
                    "contributions": json.loads(row.contributions_json),
                    "warnings": json.loads(row.warnings_json),
                } for row in rows],
            }

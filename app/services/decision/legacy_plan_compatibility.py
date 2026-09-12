import json
import logging
import time
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select

from app.core.exceptions import PlanningError
from app.core.provenance import canonical_hash
from app.models.business import IngredientModel, SupplierModel
from app.models.operations import PlanRunModel, RecommendationModel
from app.repositories.audit_logs import AuditLogRepository
from app.repositories.idempotency import IdempotencyRepository
from app.repositories.planning import PlanningRepository
from app.repositories.stores import StoreRepository
from app.schemas.planning import ProcurementPlansRequest
from app.services.audit_service import AuditService
from app.services.idempotency_service import IdempotencyService


def now():
    return datetime.now(timezone.utc)


def dump(value):
    return json.dumps(value, ensure_ascii=False, default=str)


LEGACY_STRATEGIES = {
    "economy": "lean",
    "balanced": "balanced",
    "safe": "protected",
    "lean": "lean",
    "protected": "protected",
}

logger = logging.getLogger("shelfcash.planning")


class LegacyPlanCompatibility:
    """Owns the current public /plan-runs compatibility application lifecycle."""

    def __init__(
        self,
        session_factory,
        completed_forecast_reader,
        ingredient_demand_lifecycle,
        operational_procurement_lifecycle,
    ):
        self.session_factory = session_factory
        self.completed_forecast_reader = completed_forecast_reader
        self.ingredient_demand_lifecycle = ingredient_demand_lifecycle
        self.operational_procurement_lifecycle = operational_procurement_lifecycle

    def create(self, store_id: str, body, key: str | None = None, request_id: str | None = None):
        started = time.monotonic()
        strategy = LEGACY_STRATEGIES.get(body.strategy)
        if strategy is None:
            raise PlanningError(
                "FORECAST_INPUT_INVALID",
                "strategy không hợp lệ.",
                {"allowed": sorted(LEGACY_STRATEGIES)},
            )
        endpoint = f"/api/v1/stores/{store_id}/plan-runs"
        payload = body.model_dump(mode="json")
        request_hash = canonical_hash(payload)
        logger.info(
            "legacy_plan_adapter_started request_id=%s store_id=%s forecast_run_id=%s strategy=%s budget_limit=%s",
            request_id,
            store_id,
            body.forecast_run_id,
            strategy,
            body.budget_limit,
        )
        with self.session_factory() as session:
            forecast, _ = self.completed_forecast_reader.load(session, store_id, body.forecast_run_id)
            if body.as_of_date != forecast.cutoff_date:
                raise PlanningError(
                    "FORECAST_INPUT_INVALID",
                    "as_of_date phải bằng forecast cutoff_date.",
                    {"as_of_date": body.as_of_date, "cutoff_date": forecast.cutoff_date},
                )
            if key:
                replay = IdempotencyService(IdempotencyRepository(session)).register(
                    store_id=store_id,
                    endpoint=endpoint,
                    http_method="POST",
                    idempotency_key=key,
                    request_hash=request_hash,
                )
                if replay.is_replay:
                    resource_id = replay.record.resource_id
                    session.rollback()
                    return self.read_metadata(store_id, resource_id)
            plan_run_id = str(uuid4())
            legacy = PlanRunModel(
                plan_run_id=plan_run_id,
                store_id=store_id,
                forecast_run_id=body.forecast_run_id,
                strategy=body.strategy,
                budget_limit=body.budget_limit,
                as_of_date=body.as_of_date,
                include_open_purchase_orders=body.include_open_purchase_orders,
                status="running",
                engine_status="decision_planning",
                request_hash=request_hash,
                input_snapshot_json=dump({"request": payload, "planning_strategy": strategy}),
                warnings_json="[]",
                created_at=now(),
            )
            session.add(legacy)
            if key:
                record = IdempotencyRepository(session).get(
                    store_id=store_id,
                    endpoint=endpoint,
                    http_method="POST",
                    idempotency_key=key,
                )
                record.resource_type = "plan_run"
                record.resource_id = plan_run_id
                record.response_status = 200
            session.commit()

        try:
            logger.info(
                "planning_started request_id=%s store_id=%s forecast_run_id=%s plan_run_id=%s strategy=%s",
                request_id,
                store_id,
                body.forecast_run_id,
                plan_run_id,
                strategy,
            )
            with self.session_factory() as session:
                demand = PlanningRepository(session).demand_run_for_forecast(body.forecast_run_id)
            if not demand or demand.status != "completed":
                self.ingredient_demand_lifecycle.generate(store_id, body.forecast_run_id)
            logger.info(
                "ingredient_demand_resolved request_id=%s store_id=%s forecast_run_id=%s plan_run_id=%s",
                request_id,
                store_id,
                body.forecast_run_id,
                plan_run_id,
            )
            result = self.operational_procurement_lifecycle.generate(
                store_id,
                body.forecast_run_id,
                ProcurementPlansRequest(
                    strategies=[strategy],
                    use_open_purchase_orders=body.include_open_purchase_orders,
                    use_latest_inventory=True,
                    budget_override=body.budget_limit,
                ),
            )
            procurement_plan_run_id = result["procurement_plan_run_id"]
            selected = result["plans"][0]
            logger.info(
                "inventory_simulation_completed request_id=%s store_id=%s forecast_run_id=%s plan_run_id=%s planning_run_id=%s strategy=%s",
                request_id,
                store_id,
                body.forecast_run_id,
                plan_run_id,
                procurement_plan_run_id,
                strategy,
            )
            logger.info(
                "procurement_plan_created request_id=%s store_id=%s forecast_run_id=%s plan_run_id=%s planning_run_id=%s strategy=%s",
                request_id,
                store_id,
                body.forecast_run_id,
                plan_run_id,
                procurement_plan_run_id,
                strategy,
            )
            with self.session_factory() as session:
                legacy = session.get(PlanRunModel, plan_run_id)
                legacy.procurement_plan_run_id = procurement_plan_run_id
                legacy.status = "completed"
                legacy.engine_status = "decision_planning"
                legacy.completed_at = now()
                legacy.warnings_json = dump(result["warnings"])
                for line in selected["lines"]:
                    if line["supplier_id"] and line["order_quantity"] > 0:
                        session.add(RecommendationModel(
                            recommendation_id=str(uuid4()),
                            plan_run_id=plan_run_id,
                            store_id=store_id,
                            ingredient_id=line["ingredient_id"],
                            unit=line["unit"],
                            order_quantity=Decimal(str(line["order_quantity"])),
                            unit_cost=line["unit_cost"],
                            cost=line["line_cost"],
                            supplier_id=line["supplier_id"],
                            moq=Decimal(str(line["moq"])),
                            pack_size=Decimal(str(line["pack_size"])),
                            lead_time_days=line["lead_time_days"],
                            created_at=now(),
                        ))
                AuditService(AuditLogRepository(session)).record(
                    store_id=store_id,
                    action="legacy_plan_generated",
                    resource_type="plan_run",
                    resource_id=plan_run_id,
                    after={
                        "forecast_run_id": body.forecast_run_id,
                        "procurement_plan_run_id": procurement_plan_run_id,
                        "strategy": strategy,
                    },
                    source="planning_service",
                )
                session.commit()
            logger.info(
                "legacy_plan_adapter_completed request_id=%s store_id=%s forecast_run_id=%s plan_run_id=%s planning_run_id=%s strategy=%s duration=%.3f",
                request_id,
                store_id,
                body.forecast_run_id,
                plan_run_id,
                procurement_plan_run_id,
                strategy,
                time.monotonic() - started,
            )
            return self.read_metadata(store_id, plan_run_id)
        except PlanningError as exc:
            with self.session_factory() as session:
                legacy = session.get(PlanRunModel, plan_run_id)
                if legacy:
                    legacy.status = "blocked"
                    legacy.failure_code = exc.code
                    legacy.failure_message = exc.message
                    legacy.completed_at = now()
                    session.commit()
            logger.exception(
                "legacy_plan_adapter_failed request_id=%s store_id=%s forecast_run_id=%s plan_run_id=%s strategy=%s failure_code=%s duration=%.3f",
                request_id,
                store_id,
                body.forecast_run_id,
                plan_run_id,
                strategy,
                exc.code,
                time.monotonic() - started,
            )
            raise
        except Exception as exc:
            with self.session_factory() as session:
                legacy = session.get(PlanRunModel, plan_run_id)
                if legacy:
                    legacy.status = "failed"
                    legacy.failure_code = "PROCUREMENT_PLAN_INFEASIBLE"
                    legacy.failure_message = str(exc)[:500]
                    legacy.completed_at = now()
                    session.commit()
            logger.exception(
                "legacy_plan_adapter_failed request_id=%s store_id=%s forecast_run_id=%s plan_run_id=%s strategy=%s failure_code=PROCUREMENT_PLAN_INFEASIBLE duration=%.3f",
                request_id,
                store_id,
                body.forecast_run_id,
                plan_run_id,
                strategy,
                time.monotonic() - started,
            )
            raise PlanningError("PROCUREMENT_PLAN_INFEASIBLE", "Planning execution thất bại.", http_status=500) from exc

    def read_metadata(self, store_id: str, plan_run_id: str):
        with self.session_factory() as session:
            StoreRepository(session).get_required(store_id)
            run = session.get(PlanRunModel, plan_run_id)
            if not run or run.store_id != store_id:
                raise PlanningError(
                    "PLANNING_RUN_NOT_FOUND",
                    "Không tìm thấy plan run.",
                    {"plan_run_id": plan_run_id},
                    http_status=404,
                )
            strategy = LEGACY_STRATEGIES.get(run.strategy, run.strategy)
            return {
                "plan_run_id": run.plan_run_id,
                "store_id": run.store_id,
                "forecast_run_id": run.forecast_run_id,
                "procurement_plan_run_id": run.procurement_plan_run_id,
                "status": run.status,
                "engine_status": run.engine_status,
                "strategy": run.strategy,
                "planning_strategy": strategy,
                "budget_limit": run.budget_limit,
                "as_of_date": run.as_of_date,
                "include_open_purchase_orders": run.include_open_purchase_orders,
                "created_at": run.created_at,
                "completed_at": run.completed_at,
                "result_url": f"/api/v1/stores/{store_id}/plan-runs/{plan_run_id}/result",
                "warnings": json.loads(run.warnings_json or "[]"),
                "failure_code": run.failure_code,
                "failure_message": run.failure_message,
            }

    def read_result(self, store_id: str, plan_run_id: str):
        metadata = self.read_metadata(store_id, plan_run_id)
        if metadata["status"] != "completed":
            raise PlanningError(
                metadata["failure_code"] or "PROCUREMENT_PLAN_INFEASIBLE",
                metadata["failure_message"] or "Plan run chưa completed.",
                {"plan_run_id": plan_run_id},
                http_status=409,
            )
        result = self.operational_procurement_lifecycle.read(
            store_id,
            metadata["forecast_run_id"],
            metadata["procurement_plan_run_id"],
        )
        selected = next((plan for plan in result["plans"] if plan["strategy"] == metadata["planning_strategy"]), None)
        if selected is None:
            raise PlanningError(
                "PLANNING_PERSISTENCE_INCONSISTENCY",
                "Không tìm thấy selected strategy trong planning result.",
                http_status=500,
            )
        with self.session_factory() as session:
            recommendations = {
                recommendation.ingredient_id: recommendation.recommendation_id
                for recommendation in session.scalars(select(RecommendationModel).where(
                    RecommendationModel.plan_run_id == plan_run_id,
                ))
            }
            lines = []
            for line in selected["lines"]:
                ingredient = session.get(IngredientModel, line["ingredient_id"])
                supplier = session.get(SupplierModel, line["supplier_id"]) if line["supplier_id"] else None
                lines.append({
                    **line,
                    "ingredient_name": ingredient.ingredient if ingredient else None,
                    "supplier_name": supplier.supplier if supplier else None,
                    "recommendation_id": recommendations.get(line["ingredient_id"]),
                })
        metrics = selected["metrics"]
        return {
            **metadata,
            "is_feasible": selected["is_feasible"],
            "is_recommended": selected["is_recommended"],
            "total_purchase_cost": selected["total_purchase_cost"],
            "projected_shortage_quantity": selected["projected_shortage_quantity"],
            "projected_waste_quantity": selected["projected_waste_quantity"],
            "fill_rate": selected["fill_rate"],
            "budget_used": selected["budget_used"],
            "budget_remaining": metrics.get("budget_remaining"),
            "budget_trace": metrics.get("budget_trace", {}),
            "constraint_violations": metrics.get("constraint_violations", []),
            "warnings": sorted(set(metadata["warnings"] + selected["warnings"])),
            "storage_capacity_trace": metrics.get("storage_capacity_trace", {}),
            "shelf_life_trace": metrics.get("shelf_life_trace", {}),
            "plan_lines": lines,
            "simulation_summary": selected["daily_projections"],
        }

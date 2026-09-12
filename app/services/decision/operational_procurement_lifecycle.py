import json
from datetime import datetime, timezone
from uuid import uuid4

from app.core.exceptions import PlanningError
from app.core.provenance import canonical_hash
from app.models.planning import ProcurementPlanLineModel, ProcurementPlanModel, ProcurementPlanRunModel
from app.repositories.audit_logs import AuditLogRepository
from app.repositories.idempotency import IdempotencyRepository
from app.repositories.planning import PlanningRepository
from app.services.audit_service import AuditService
from app.services.idempotency_service import IdempotencyService
from app.services.procurement_planning_service import ProcurementPlanningService


def now():
    return datetime.now(timezone.utc)


def dump(value):
    return json.dumps(value, ensure_ascii=False, default=str)


class OperationalProcurementLifecycle:
    """Owns the current Operational Procurement application command and read lifecycle."""

    def __init__(self, session_factory, completed_forecast_reader):
        self.session_factory = session_factory
        self.completed_forecast_reader = completed_forecast_reader

    def generate(self, store_id: str, forecast_run_id: str, body, key: str | None = None):
        endpoint = f"/api/v1/stores/{store_id}/forecast-runs/{forecast_run_id}/procurement-plans"
        payload = body.model_dump(mode="json")
        request_hash = canonical_hash(payload)
        if len(body.strategies) != len(set(body.strategies)):
            raise PlanningError("FORECAST_INPUT_INVALID", "strategies bị trùng.")
        if not body.use_latest_inventory:
            raise PlanningError(
                "FORECAST_INPUT_INVALID",
                "Backend chỉ hỗ trợ persisted latest inventory; what-if inventory chưa được hỗ trợ.",
            )
        with self.session_factory() as session:
            forecast, _ = self.completed_forecast_reader.load(session, store_id, forecast_run_id)
            repository = PlanningRepository(session)
            demand_run = repository.demand_run_for_forecast(forecast_run_id)
            if not demand_run or demand_run.status != "completed":
                raise PlanningError("INGREDIENT_DEMAND_INCOMPLETE", "Ingredient demand chưa completed.", http_status=409)
            demands = repository.demand_predictions(demand_run.ingredient_demand_run_id)
            if not demands:
                if "INGREDIENT_SCOPE_NO_MATCH" in json.loads(demand_run.warnings_json or "[]"):
                    raise PlanningError("INGREDIENT_DEMAND_INCOMPLETE", "Ingredient scope không có demand để lập plan.")
                raise PlanningError("INGREDIENT_DEMAND_INCOMPLETE", "Thiếu ingredient demand.", http_status=500)
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
                    return self.read(store_id, forecast_run_id, resource_id)
            planning_run = ProcurementPlanRunModel(
                procurement_plan_run_id=str(uuid4()),
                forecast_run_id=forecast_run_id,
                ingredient_demand_run_id=demand_run.ingredient_demand_run_id,
                store_id=store_id,
                status="running",
                request_json=dump(payload),
                warnings_json="[]",
                created_at=now(),
            )
            session.add(planning_run)
            session.flush()
            if key:
                record = IdempotencyRepository(session).get(
                    store_id=store_id,
                    endpoint=endpoint,
                    http_method="POST",
                    idempotency_key=key,
                )
                record.resource_type = "procurement_plan_run"
                record.resource_id = planning_run.procurement_plan_run_id
            session.commit()

            strategy_source = "explicit" if "strategies" in body.model_fields_set else "request_default"
            try:
                plans, recommended = ProcurementPlanningService(session).build(
                    store_id,
                    forecast,
                    demands,
                    body.strategies,
                    body.use_open_purchase_orders,
                    body.budget_override,
                    strategy_source,
                )
            except PlanningError as exc:
                failed = session.get(ProcurementPlanRunModel, planning_run.procurement_plan_run_id)
                failed.status = "blocked"
                failed.failure_code = exc.code
                failed.failure_message = exc.message
                failed.completed_at = now()
                session.commit()
                raise
            except Exception as exc:
                failed = session.get(ProcurementPlanRunModel, planning_run.procurement_plan_run_id)
                failed.status = "failed"
                failed.failure_code = "PROCUREMENT_PLAN_INFEASIBLE"
                failed.failure_message = str(exc)[:500]
                failed.completed_at = now()
                session.commit()
                raise PlanningError(
                    "PROCUREMENT_PLAN_INFEASIBLE",
                    "Procurement planning thất bại.",
                    http_status=500,
                ) from exc

            all_warnings = sorted(set(
                json.loads(demand_run.warnings_json or "[]") + [warning for plan in plans for warning in plan["warnings"]],
            ))
            for plan_data in plans:
                plan = ProcurementPlanModel(
                    procurement_plan_id=str(uuid4()),
                    procurement_plan_run_id=planning_run.procurement_plan_run_id,
                    strategy=plan_data["strategy"],
                    is_feasible=plan_data["is_feasible"],
                    is_recommended=plan_data["is_recommended"],
                    total_purchase_cost=plan_data["total_purchase_cost"],
                    projected_shortage_quantity=plan_data["projected_shortage_quantity"],
                    projected_waste_quantity=plan_data["projected_waste_quantity"],
                    fill_rate=plan_data["fill_rate"],
                    budget_used=plan_data["budget_used"],
                    metrics_json=dump({key: value for key, value in plan_data.items() if key not in {"lines", "daily_projections", "warnings"}}),
                    daily_projections_json=dump(plan_data["daily_projections"]),
                    warnings_json=dump(plan_data["warnings"]),
                    created_at=now(),
                )
                session.add(plan)
                session.flush()
                for line in plan_data["lines"]:
                    session.add(ProcurementPlanLineModel(
                        procurement_plan_line_id=str(uuid4()),
                        procurement_plan_id=plan.procurement_plan_id,
                        ingredient_id=line["ingredient_id"],
                        supplier_id=line["supplier_id"],
                        supplier_term_id=line["supplier_term_id"],
                        order_date=line["order_date"],
                        expected_arrival_date=line["expected_arrival_date"],
                        raw_required_quantity=line["raw_required_quantity"],
                        order_quantity=line["order_quantity"],
                        unit=line["unit"],
                        pack_count=line["pack_count"],
                        unit_cost=line["unit_cost"],
                        line_cost=line["line_cost"],
                        moq=line["moq"],
                        pack_size=line["pack_size"],
                        lead_time_days=line["lead_time_days"],
                        reason_codes_json=dump(line["reason_codes"]),
                        warnings_json=dump(line["warnings"]),
                        created_at=now(),
                    ))
            planning_run.status = "completed"
            planning_run.completed_at = now()
            planning_run.recommended_strategy = recommended
            planning_run.warnings_json = dump(all_warnings)
            if key:
                record = IdempotencyRepository(session).get(
                    store_id=store_id,
                    endpoint=endpoint,
                    http_method="POST",
                    idempotency_key=key,
                )
                record.resource_type = "procurement_plan_run"
                record.resource_id = planning_run.procurement_plan_run_id
                record.response_status = 200
            AuditService(AuditLogRepository(session)).record(
                store_id=store_id,
                action="procurement_plans_generated",
                resource_type="procurement_plan_run",
                resource_id=planning_run.procurement_plan_run_id,
                after={"forecast_run_id": forecast_run_id, "recommended_strategy": recommended},
                source="planning_service",
            )
            session.commit()
            resource_id = planning_run.procurement_plan_run_id
        return self.read(store_id, forecast_run_id, resource_id)

    def read(self, store_id: str, forecast_run_id: str, procurement_plan_run_id: str | None = None):
        with self.session_factory() as session:
            self.completed_forecast_reader.load(session, store_id, forecast_run_id)
            repository = PlanningRepository(session)
            run = (
                repository.plan_run(procurement_plan_run_id)
                if procurement_plan_run_id
                else repository.latest_plan_run(forecast_run_id)
            )
            if not run or run.store_id != store_id or run.forecast_run_id != forecast_run_id:
                raise PlanningError("PLANNING_RUN_NOT_FOUND", "Không tìm thấy planning run.", http_status=404)
            plans = repository.plans(run.procurement_plan_run_id)
            if run.status == "completed" and not plans:
                raise PlanningError("PLANNING_PERSISTENCE_INCONSISTENCY", "Completed planning run thiếu plans.", http_status=500)
            result_plans = []
            for plan in plans:
                metrics = json.loads(plan.metrics_json)
                result_plans.append({
                    "procurement_plan_id": plan.procurement_plan_id,
                    "strategy": plan.strategy,
                    "is_feasible": plan.is_feasible,
                    "is_recommended": plan.is_recommended,
                    "total_purchase_cost": plan.total_purchase_cost,
                    "projected_shortage_quantity": float(plan.projected_shortage_quantity),
                    "projected_waste_quantity": float(plan.projected_waste_quantity),
                    "fill_rate": float(plan.fill_rate),
                    "budget_used": plan.budget_used,
                    "metrics": metrics,
                    "warnings": json.loads(plan.warnings_json),
                    "daily_projections": json.loads(plan.daily_projections_json),
                    "lines": [{
                        "ingredient_id": line.ingredient_id,
                        "supplier_id": line.supplier_id,
                        "supplier_term_id": line.supplier_term_id,
                        "order_date": line.order_date,
                        "expected_arrival_date": line.expected_arrival_date,
                        "raw_required_quantity": float(line.raw_required_quantity),
                        "order_quantity": float(line.order_quantity),
                        "rounding_excess": float(line.order_quantity - line.raw_required_quantity),
                        "unit": line.unit,
                        "pack_count": line.pack_count,
                        "unit_cost": line.unit_cost,
                        "line_cost": line.line_cost,
                        "moq": float(line.moq) if line.moq is not None else None,
                        "pack_size": float(line.pack_size) if line.pack_size is not None else None,
                        "lead_time_days": line.lead_time_days,
                        "reason_codes": json.loads(line.reason_codes_json),
                        "warnings": json.loads(line.warnings_json),
                    } for line in repository.lines(plan.procurement_plan_id)],
                })
            return {
                "procurement_plan_run_id": run.procurement_plan_run_id,
                "forecast_run_id": forecast_run_id,
                "ingredient_demand_run_id": run.ingredient_demand_run_id,
                "store_id": store_id,
                "status": run.status,
                "recommended_strategy": run.recommended_strategy,
                "warnings": json.loads(run.warnings_json or "[]"),
                "failure_code": run.failure_code,
                "failure_message": run.failure_message,
                "created_at": run.created_at,
                "completed_at": run.completed_at,
                "plans": result_plans,
            }

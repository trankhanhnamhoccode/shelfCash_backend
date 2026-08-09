import json
from datetime import date,datetime,timezone
from decimal import Decimal

from app.models.operations import ForecastRunModel
from app.models.planning import IngredientDemandRunModel,ProcurementPlanModel,ProcurementPlanRunModel
from app.models.audit_log import AuditLogModel
from sqlalchemy import select


def ingredient(client):
    items=client.get("/api/v1/stores/STORE_001/ingredients").json()
    if not items:
        created=client.post("/api/v1/stores/STORE_001/ingredients",json={"ingredient":"Constraint API ingredient",
            "sku":"CONSTRAINT_API","base_unit":"kg","active":True})
        assert created.status_code==201,created.text
        return created.json()
    return items[0]


def test_create_store_and_ingredient_constraints_validation_and_idempotency(client):
    store_payload={"ingredient_id":None,"constraint_type":"budget","value":500000,"currency":"VND",
        "effective_date":"2026-08-01","note":"API budget"}
    first=client.post("/api/v1/stores/STORE_001/inventory-constraints",json=store_payload,
        headers={"Idempotency-Key":"constraint-budget-create"})
    assert first.status_code==201,first.text
    assert Decimal(first.json()["constraint"]["value"])==Decimal("500000")
    assert first.json()["constraint"]["currency"]=="VND" and first.json()["constraint"]["version"]==1
    replay=client.post("/api/v1/stores/STORE_001/inventory-constraints",json=store_payload,
        headers={"Idempotency-Key":"constraint-budget-create"})
    assert replay.status_code==201 and replay.json()==first.json()

    item=ingredient(client);iid=item["ingredient_id"];unit=item["base_unit"];payload={"ingredient_id":iid,"constraint_type":"reorder point","value":12,
        "unit":unit,"effective_date":"2026-08-01","note":"direct API"}
    created=client.post("/api/v1/stores/STORE_001/inventory-constraints",json=payload)
    assert created.status_code==201,created.text
    assert created.json()["constraint"]["constraint_type"]=="reorder_point"
    assert created.json()["constraint"]["unit"]==unit
    duration=client.post("/api/v1/stores/STORE_001/inventory-constraints",json={"ingredient_id":iid,
        "constraint_type":"shelf_life_target","value":7,"unit":"days","effective_date":"2026-08-01"})
    assert duration.status_code==201 and duration.json()["constraint"]["unit"]=="day"

    invalid_scope=client.post("/api/v1/stores/STORE_001/inventory-constraints",json={
        "ingredient_id":None,"constraint_type":"safety_stock","value":1,"unit":"kg","effective_date":"2026-08-01"})
    assert invalid_scope.status_code==404 or invalid_scope.status_code==422
    invalid_unit=client.post("/api/v1/stores/STORE_001/inventory-constraints",json={
        "ingredient_id":iid,"constraint_type":"safety_stock","value":1,"unit":"day","effective_date":"2026-08-01"})
    assert invalid_unit.status_code==422 and invalid_unit.json()["code"]=="BUSINESS_CONSTRAINT_UNIT_INVALID"


def test_version_correction_deactivate_ownership_and_optimistic_concurrency(client):
    item=ingredient(client);iid=item["ingredient_id"];unit=item["base_unit"];base={"ingredient_id":iid,"constraint_type":"minimum_stock","value":2,
        "unit":unit,"effective_date":"2026-08-01","note":"v1"}
    created=client.post("/api/v1/stores/STORE_001/inventory-constraints",json=base).json()["constraint"]
    updated=client.patch(f"/api/v1/stores/STORE_001/inventory-constraints/{created['constraint_id']}",json={
        "expected_version":1,"value":3,"unit":unit,"effective_date":"2026-08-10","note":"v2"})
    assert updated.status_code==200,updated.text
    body=updated.json();assert body["constraint"]["version"]==2 and len(body["history"])==2
    old=next(item for item in body["history"] if item["version"]==1)
    assert old["end_date"]=="2026-08-09" and old["active"] is False

    stale=client.patch(f"/api/v1/stores/STORE_001/inventory-constraints/{body['constraint']['constraint_id']}",json={
        "expected_version":1,"value":4,"unit":unit,"effective_date":"2026-08-11"})
    assert stale.status_code==409 and stale.json()["code"]=="VERSION_CONFLICT"
    wrong_store=client.patch(f"/api/v1/stores/STORE_TEST_001/inventory-constraints/{body['constraint']['constraint_id']}",json={
        "expected_version":2,"value":4,"unit":unit,"effective_date":"2026-08-11"})
    assert wrong_store.status_code==404

    current_id=body["constraint"]["constraint_id"]
    corrected=client.patch(f"/api/v1/stores/STORE_001/inventory-constraints/{current_id}",json={
        "expected_version":2,"value":4,"unit":unit,"effective_date":"2026-08-10",
        "correction_mode":"replace_same_effective_date","note":"audited correction"})
    assert corrected.status_code==200,corrected.text
    correction=corrected.json();assert correction["constraint"]["version"]==3
    replaced=next(item for item in correction["history"] if item["version"]==2)
    assert replaced["superseded_by_constraint_id"]==correction["constraint"]["constraint_id"]

    deactivated=client.post(f"/api/v1/stores/STORE_001/inventory-constraints/{correction['constraint']['constraint_id']}/deactivate",json={
        "expected_version":3,"end_date":"2026-08-31","note":"closed"})
    assert deactivated.status_code==200
    assert deactivated.json()["constraint"]["active"] is False and deactivated.json()["constraint"]["end_date"]=="2026-08-31"
    with client.app.state.session_factory() as session:
        actions=set(session.scalars(select(AuditLogModel.action).where(AuditLogModel.resource_type=="inventory_constraint")))
        assert {"inventory_constraint_created","inventory_constraint_version_created",
            "inventory_constraint_corrected","inventory_constraint_deactivated"}.issubset(actions)


def test_same_date_correction_is_blocked_after_completed_plan(client):
    item=ingredient(client);iid=item["ingredient_id"];unit=item["base_unit"];created=client.post("/api/v1/stores/STORE_001/inventory-constraints",json={
        "ingredient_id":iid,"constraint_type":"maximum_stock","value":20,"unit":unit,"effective_date":"2026-08-01"}).json()["constraint"]
    now=datetime.now(timezone.utc)
    with client.app.state.session_factory() as session:
        session.add(ForecastRunModel(forecast_run_id="constraint-ref-forecast",store_id="STORE_001",cutoff_date=date(2026,8,4),
            horizon_days=1,quantiles_json="[0.25,0.5,0.75]",scope_json="{}",use_latest_calendar=True,status="completed",
            engine_status="forecast_core",request_hash="constraint-ref",warnings_json="[]",created_at=now,completed_at=now))
        session.add(IngredientDemandRunModel(ingredient_demand_run_id="constraint-ref-demand",forecast_run_id="constraint-ref-forecast",
            store_id="STORE_001",status="completed",warnings_json="[]",created_at=now,completed_at=now));session.flush()
        session.add(ProcurementPlanRunModel(procurement_plan_run_id="constraint-ref-run",forecast_run_id="constraint-ref-forecast",
            ingredient_demand_run_id="constraint-ref-demand",store_id="STORE_001",status="completed",request_json="{}",
            warnings_json="[]",created_at=now,completed_at=now));session.flush()
        session.add(ProcurementPlanModel(procurement_plan_id="constraint-ref-plan",procurement_plan_run_id="constraint-ref-run",
            strategy="balanced",is_feasible=True,is_recommended=True,total_purchase_cost=0,projected_shortage_quantity=0,
            projected_waste_quantity=0,fill_rate=1,budget_used=0,
            metrics_json=json.dumps({"constraint_trace":{iid:{"maximum_stock":"20"}}}),daily_projections_json="[]",
            warnings_json="[]",created_at=now));session.commit()
    blocked=client.patch(f"/api/v1/stores/STORE_001/inventory-constraints/{created['constraint_id']}",json={
        "expected_version":1,"value":21,"unit":unit,"effective_date":"2026-08-01",
        "correction_mode":"replace_same_effective_date"})
    assert blocked.status_code==409 and blocked.json()["code"]=="BUSINESS_CONSTRAINT_CORRECTION_BLOCKED"

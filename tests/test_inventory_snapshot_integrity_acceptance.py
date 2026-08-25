"""Acceptance coverage for the 20260821 inventory snapshot integrity restore.

These tests intentionally drive the persistence service directly where that is
the domain boundary.  That makes the mutation assertions independent of HTTP
serialization while still using the production session, models and Alembic
upgrade path.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, select

from app.core.business_time import snapshot_eod_boundary
from app.core.exceptions import InventorySnapshotError, ValidationError
from app.db.session import create_engine_from_url, create_session_factory
from app.models.business import IngredientModel, InventoryLotModel, InventoryMovementModel, SupplierIngredientTermModel
from app.models.business import SupplierModel
from app.models.operations import ForecastRunModel, PlanRunModel, PurchaseOrderLineModel, PurchaseOrderModel
from app.services.business_persistence import ImportBusinessPersistenceService
from app.services.procurement_planning_service import ProcurementPlanningService
from scripts.seed_database import seed_database
from shelfcash_core.inventory.contracts import InventoryLot
from shelfcash_core.inventory.fefo import fefo_sort_key


STORE = "STORE_001"


def _service(session, rows, *, import_id=None):
    return ImportBusinessPersistenceService(session), SimpleNamespace(
        store_id=STORE, import_id=import_id, forecast_date=date(2026, 8, 20), created_at=datetime.now(timezone.utc)
    ), {"sheet_type": "inventory", "profile_id": None, "sheet_id": "acceptance", "rows": rows}


def _row(**changes):
    result = {
        "snapshot_date": "2026-08-20", "ingredient_name": "Acceptance milk", "on_hand": "20",
        "unit": "kg", "batch_id": "B1", "received_date": "2026-08-15",
        "supplier_name": "Acceptance supplier", "expiry_date": "2026-08-25", "warehouse_name": "Cold",
    }
    result.update(changes)
    return result


def _persist(session, rows, *, import_id=None):
    service, job, sheet = _service(session, rows, import_id=import_id)
    service._persist_inventory(job, sheet)
    session.flush()
    return service


def _counts(session):
    return (
        session.scalar(select(func.count()).select_from(InventoryLotModel)),
        session.scalar(select(func.count()).select_from(InventoryMovementModel)),
    )


def test_snapshot_acceptance_quantity_and_metadata_matrix(session_factory):
    """Declared/unknown creation, idempotency, deltas and conflict safety."""
    with session_factory() as session:
        seed_database(session_factory)
        _persist(session, [_row()], import_id="same-import")
        lot = session.scalar(select(InventoryLotModel))
        movement = session.scalar(select(InventoryMovementModel))
        assert (lot.received_date, lot.received_date_status, movement.quantity_delta) == (date(2026, 8, 15), "declared", Decimal("20"))
        # SQLite stores DateTime without an offset; its value is still the
        # UTC instant of the next local midnight (2026-08-20 17:00Z).
        assert movement.occurred_at.replace(tzinfo=timezone.utc) == snapshot_eod_boundary(date(2026, 8, 20), "Asia/Ho_Chi_Minh")

        # Exact replay is a no-op; a later snapshot adjusts only the delta.
        _persist(session, [_row()], import_id="same-import")
        assert _counts(session) == (1, 1)
        _persist(session, [_row(snapshot_date="2026-08-21", on_hand="16")], import_id="second")
        _persist(session, [_row(snapshot_date="2026-08-22", on_hand="25")], import_id="third")
        movements = list(session.scalars(select(InventoryMovementModel).order_by(InventoryMovementModel.occurred_at)))
        assert [x.quantity_delta for x in movements] == [Decimal("20"), Decimal("-4"), Decimal("9")]

        # All metadata comparisons happen before a quantity movement is written.
        before = _counts(session)
        with pytest.raises(InventorySnapshotError, match="metadata") as caught:
            _persist(session, [_row(snapshot_date="2026-08-22", on_hand="99", unit="g")], import_id="bad-unit")
        assert caught.value.code == "INVENTORY_LOT_METADATA_CONFLICT"
        assert _counts(session) == before
        assert session.get(InventoryLotModel, lot.lot_id).unit == "kg"


@pytest.mark.parametrize("change", [
    {"supplier_name": "Other supplier"}, {"expiry_date": "2026-08-28"},
    {"received_date": "2026-08-16"}, {"warehouse_name": "Other warehouse"},
])
def test_snapshot_metadata_conflicts_are_non_mutating(session_factory, change):
    with session_factory() as session:
        seed_database(session_factory)
        _persist(session, [_row()], import_id="one")
        before = _counts(session)
        with pytest.raises(InventorySnapshotError) as caught:
            _persist(session, [_row(snapshot_date="2026-08-21", on_hand="1", **change)], import_id="two")
        assert caught.value.code == "INVENTORY_LOT_METADATA_CONFLICT"
        assert _counts(session) == before


def test_unknown_receipt_duplicate_batch_and_cross_ingredient_identity(session_factory):
    with session_factory() as session:
        seed_database(session_factory)
        _persist(session, [_row(batch_id="UNKNOWN", received_date=None)], import_id="unknown")
        unknown = session.scalar(select(InventoryLotModel).where(InventoryLotModel.batch_code == "UNKNOWN"))
        assert (unknown.received_date, unknown.received_date_status) == (None, "unknown")
        _persist(session, [_row(batch_id="UNKNOWN", received_date=None, snapshot_date="2026-08-21")], import_id="unknown-2")
        with pytest.raises(InventorySnapshotError) as caught:
            _persist(session, [_row(batch_id=None)], import_id="missing")
        assert caught.value.code == "INVENTORY_BATCH_ID_REQUIRED"
        before = _counts(session)
        with pytest.raises(InventorySnapshotError) as caught:
            _persist(session, [_row(batch_id="DUP"), _row(batch_id="DUP")], import_id="dup")
        assert caught.value.code == "INVENTORY_SNAPSHOT_DUPLICATE_BATCH"
        assert _counts(session) == before
        _persist(session, [_row(ingredient_name="Acceptance cream", batch_id="UNKNOWN")], import_id="other-ing")
        assert session.scalar(select(func.count()).select_from(InventoryLotModel).where(InventoryLotModel.batch_code == "UNKNOWN")) == 2


def test_normalized_duplicate_batch_is_preflighted_without_partial_write(session_factory):
    with session_factory() as session:
        seed_database(session_factory)
        before = _counts(session)
        with pytest.raises(InventorySnapshotError) as caught:
            _persist(session, [_row(ingredient_name="Acceptance Milk", batch_id="CASE"), _row(ingredient_name="acceptance milk", batch_id="CASE")], import_id="dupe")
        assert caught.value.code == "INVENTORY_SNAPSHOT_DUPLICATE_BATCH"
        assert _counts(session) == before


def test_snapshot_chronology_is_local_eod_not_utc_date(session_factory):
    with session_factory() as session:
        seed_database(session_factory)
        _persist(session, [_row(snapshot_date="2026-08-19")], import_id="initial")
        lot = session.scalar(select(InventoryLotModel))
        # 18:30 UTC is Aug 21 01:30 in the store.  It makes an Aug 20 count stale.
        session.add(InventoryMovementModel(movement_id="next-local-day", store_id=STORE, lot_id=lot.lot_id,
            movement_type="receipt", quantity_delta=Decimal("1"), unit="kg", occurred_at=datetime(2026, 8, 20, 18, 30, tzinfo=timezone.utc), source="manual"))
        session.flush()
        with pytest.raises(InventorySnapshotError) as caught:
            _persist(session, [_row(on_hand="21")], import_id="stale")
        assert caught.value.code == "INVENTORY_SNAPSHOT_OUT_OF_ORDER"
        # A same-business-day exact movement is compatible with EOD count.
        session.rollback()


def test_same_local_day_exact_movement_is_accepted_at_snapshot_eod(session_factory):
    with session_factory() as session:
        seed_database(session_factory)
        _persist(session, [_row(snapshot_date="2026-08-19")], import_id="initial")
        lot = session.scalar(select(InventoryLotModel))
        # 10:00 local is before the EOD observation for Aug 20.
        session.add(InventoryMovementModel(movement_id="same-local-day", store_id=STORE, lot_id=lot.lot_id,
            movement_type="receipt", quantity_delta=Decimal("1"), unit="kg", occurred_at=datetime(2026, 8, 20, 3, 0, tzinfo=timezone.utc), source="manual"))
        session.flush()
        _persist(session, [_row(on_hand="21")], import_id="same-day")
        # Receipt already brings the balance to the EOD count, so acceptance
        # does not create a redundant zero-delta adjustment.
        assert session.scalar(select(func.count()).select_from(InventoryMovementModel)) == 2


def test_fefo_null_received_dates_are_deterministic():
    lots = [
        InventoryLot(lot_id="later", store_id="s", ingredient_id="i", quantity_remaining=1, unit="kg", expiry_date=date(2026, 8, 30), received_date=date(2026, 8, 2)),
        InventoryLot(lot_id="known", store_id="s", ingredient_id="i", quantity_remaining=1, unit="kg", expiry_date=date(2026, 8, 30), received_date=date(2026, 8, 1)),
        InventoryLot(lot_id="unknown-b", store_id="s", ingredient_id="i", quantity_remaining=1, unit="kg", expiry_date=date(2026, 8, 30), received_date=None),
        InventoryLot(lot_id="unknown-a", store_id="s", ingredient_id="i", quantity_remaining=1, unit="kg", expiry_date=date(2026, 8, 30), received_date=None),
        InventoryLot(lot_id="earlier-expiry", store_id="s", ingredient_id="i", quantity_remaining=1, unit="kg", expiry_date=date(2026, 8, 29), received_date=None),
    ]
    assert [x.lot_id for x in sorted(lots, key=fefo_sort_key)] == ["earlier-expiry", "known", "later", "unknown-a", "unknown-b"]


def test_supplier_missing_fields_are_not_zero_and_zero_is_valid(session_factory):
    with session_factory() as session:
        seed_database(session_factory)
        service, job, _ = _service(session, [])
        base = {"supplier_name": "terms", "ingredient_name": "terms ingredient", "minimum_order_quantity": "1", "order_unit": "case", "package_size": "1", "package_base_unit": "kg"}
        for missing in ("unit_price", "lead_time_days"):
            row = dict(base, unit_price="0", lead_time_days="0")
            row.pop(missing)
            with pytest.raises(ValidationError):
                service._persist_supplier_constraints(job, {"profile_id": None, "rows": [row]})
            session.rollback()
        service._persist_supplier_constraints(job, {"profile_id": None, "rows": [dict(base, unit_price="0", lead_time_days="0")]})
        term = session.scalar(select(SupplierIngredientTermModel))
        assert (term.unit_cost, term.lead_time_days, term.shelf_life_days) == (0, 0, None)


def test_real_populated_0022_upgrade_and_refused_downgrade(tmp_path):
    path = tmp_path / "populated-0022.db"
    url = f"sqlite:///{path.as_posix()}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "20260812_0022")
    engine = create_engine_from_url(url)
    factory = create_session_factory(engine)
    seed_database(factory)
    now = datetime(2026, 8, 20, tzinfo=timezone.utc)
    with engine.begin() as c:
        c.exec_driver_sql("INSERT INTO ingredients (ingredient_id,store_id,ingredient,normalized_name,base_unit,active,source,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)", ("legacy-i", STORE, "Legacy", "legacy", "kg", 1, "import", now, now))
        c.exec_driver_sql("INSERT INTO suppliers (supplier_id,store_id,supplier,normalized_name,active,source,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)", ("legacy-s", STORE, "Legacy supplier", "legacy supplier", 1, "import", now, now))
        values = [("legacy-import", "import", "2026-08-20"), ("legacy-po", "purchase_order", "2026-08-15"), ("legacy-manual", "manual", "2026-08-14")]
        for lot_id, source, received in values:
            c.exec_driver_sql("INSERT INTO inventory_lots (lot_id,store_id,ingredient_id,supplier_id,batch_code,received_date,expiry_date,initial_quantity,unit,source,version,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (lot_id, STORE, "legacy-i", "legacy-s", lot_id, received, "2026-08-30", 10, "kg", source, 1, now, now))
        c.exec_driver_sql("INSERT INTO inventory_movements (movement_id,store_id,lot_id,movement_type,quantity_delta,unit,occurred_at,source,created_at) VALUES (?,?,?,?,?,?,?,?,?)", ("legacy-move", STORE, "legacy-import", "opening_balance", 10, "kg", now, "import", now))
        c.exec_driver_sql("INSERT INTO supplier_ingredient_terms (constraint_id,store_id,supplier_id,ingredient_id,unit_cost,moq,pack_size,lead_time_days,shelf_life_days,unit,version,active,source,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("legacy-term", STORE, "legacy-s", "legacy-i", 11, 1, 1, 2, None, "kg", 1, 1, "import", now, now))
    engine.dispose()
    command.upgrade(config, "head")
    engine = create_engine_from_url(url)
    factory = create_session_factory(engine)
    with factory() as session:
        lots = {x.lot_id: x for x in session.scalars(select(InventoryLotModel))}
        assert (lots["legacy-import"].received_date, lots["legacy-import"].received_date_status) == (None, "legacy_unknown")
        assert (lots["legacy-po"].received_date, lots["legacy-po"].received_date_status) == (date(2026, 8, 15), "declared")
        assert (lots["legacy-manual"].received_date, lots["legacy-manual"].received_date_status) == (date(2026, 8, 14), "legacy_unknown")
        assert session.scalar(select(InventoryMovementModel.quantity_delta).where(InventoryMovementModel.movement_id == "legacy-move")) == Decimal("10")
        assert ProcurementPlanningService(session)._terms(STORE, "legacy-i")[0].unit_cost == 11
        # Current application persistence can consume upgraded data without an
        # ORM/schema mismatch and creates a normal post-upgrade snapshot lot.
        _persist(session, [_row(ingredient_name="Post upgrade", batch_id="POST", supplier_name="Post supplier")])
        assert session.scalar(select(InventoryLotModel).where(InventoryLotModel.batch_code == "POST")) is not None
    command.upgrade(config, "head")  # idempotent head/startup path
    with pytest.raises(RuntimeError, match="fabricating unknown received_date"):
        command.downgrade(config, "20260812_0022")
    engine.dispose()


def test_audit_tool_is_json_and_read_only(database_url, session_factory):
    with session_factory() as session:
        seed_database(session_factory)
        _persist(session, [_row(batch_id="AUDIT")])
        before = _counts(session)
        session.commit()
    env = dict(os.environ, DATABASE_URL=database_url)
    result = subprocess.run([sys.executable, "scripts/audit_inventory_received_dates.py", "--json"], cwd=Path.cwd(), env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert len(payload["lots"]["declared"]) == 1
    with session_factory() as session:
        assert _counts(session) == before


def test_audit_tool_classifies_receipt_provenance_categories(database_url, session_factory):
    now = datetime(2026, 8, 20, tzinfo=timezone.utc)
    with session_factory() as session:
        seed_database(session_factory)
        ingredient = IngredientModel(ingredient_id="audit-ing", store_id=STORE, ingredient="Audit", normalized_name="audit", base_unit="kg", active=True, source="test")
        session.add(ingredient)
        for lot_id, source, received, status in [
            ("declared", "manual", date(2026, 8, 1), "declared"),
            ("unknown", "manual", None, "unknown"),
            ("legacy-unknown", "manual", None, "legacy_unknown"),
            ("legacy-import", "import", None, "legacy_unknown"),
            ("po-receipt", "purchase_order", date(2026, 8, 2), "declared"),
            ("suspicious", "manual", date(2026, 8, 3), "legacy_unknown"),
        ]:
            session.add(InventoryLotModel(lot_id=lot_id, store_id=STORE, ingredient_id="audit-ing", batch_code=lot_id, received_date=received, received_date_status=status, initial_quantity=Decimal("1"), unit="kg", source=source, version=1, created_at=now, updated_at=now))
        session.commit()
    result = subprocess.run([sys.executable, "scripts/audit_inventory_received_dates.py", "--json"], cwd=Path.cwd(), env=dict(os.environ, DATABASE_URL=database_url), text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    categories = json.loads(result.stdout)["lots"]
    assert {key: {x["lot_id"] for x in value} for key, value in categories.items()} == {
        "declared": {"declared", "po-receipt"}, "provably_legacy_fallback": {"legacy-import"},
        "unverifiable": {"unknown", "legacy-unknown"}, "suspicious": {"suspicious"},
    }
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(InventoryLotModel)) == 6


def test_snapshot_eod_uses_next_local_midnight_across_dst():
    # DST starts in New York on 2026-03-08: next local midnight is 04:00Z,
    # not the 05:00Z instant produced by adding 24 UTC hours to prior midnight.
    assert snapshot_eod_boundary(date(2026, 3, 8), "America/New_York") == datetime(2026, 3, 9, 4, 0, tzinfo=timezone.utc)


def test_future_snapshot_is_local_date_checked_without_mutation(session_factory):
    with session_factory() as session:
        seed_database(session_factory)
        from app.core.business_time import local_business_date
        from datetime import timedelta
        today = local_business_date(datetime.now(timezone.utc), "Asia/Ho_Chi_Minh")
        _persist(session, [_row(snapshot_date=today.isoformat(), batch_id="TODAY")])
        before = _counts(session)
        with pytest.raises(InventorySnapshotError) as caught:
            _persist(session, [_row(snapshot_date=(today + timedelta(days=1)).isoformat(), batch_id="FUTURE")])
        assert caught.value.code == "INVENTORY_SNAPSHOT_FUTURE_DATE"
        assert _counts(session) == before


def test_open_po_cutoff_authority_and_quantity_conservation(session_factory):
    """Only future, unreceived remaining PO quantity is modeled as inbound."""
    cutoff = date(2026, 8, 20)
    with session_factory() as session:
        seed_database(session_factory)
        session.add_all([
            IngredientModel(ingredient_id="po-ing", store_id=STORE, ingredient="PO ingredient", normalized_name="po ingredient", base_unit="kg", active=True, source="test"),
            SupplierModel(supplier_id="po-sup", store_id=STORE, supplier="PO supplier", normalized_name="po supplier", active=True, source="test"),
        ])
        session.add(ForecastRunModel(forecast_run_id="po-forecast", store_id=STORE, cutoff_date=cutoff, horizon_days=7,
            quantiles_json="[0.5]", scope_json="{}", use_latest_calendar=True, status="completed", engine_status="test", request_hash="po"))
        session.add(PlanRunModel(plan_run_id="po-plan", store_id=STORE, forecast_run_id="po-forecast", strategy="balanced", budget_limit=0,
            as_of_date=cutoff, include_open_purchase_orders=True, status="completed", engine_status="test", request_hash="po", warnings_json="[]"))
        session.flush()
        # Confirmed actual receipt: 10 opening + 5 received = 15 EOD stock.
        session.add(InventoryLotModel(lot_id="po-received-lot", store_id=STORE, ingredient_id="po-ing", supplier_id="po-sup", batch_code="PO-RECEIVED", received_date=cutoff, received_date_status="declared", initial_quantity=Decimal("5"), unit="kg", source="purchase_order", version=1))
        session.add(InventoryMovementModel(movement_id="po-received-move", store_id=STORE, lot_id="po-received-lot", movement_type="receipt", quantity_delta=Decimal("5"), unit="kg", occurred_at=datetime(2026, 8, 20, 3, tzinfo=timezone.utc), source="purchase_order", source_id="po-received"))
        session.add(InventoryLotModel(lot_id="po-opening", store_id=STORE, ingredient_id="po-ing", batch_code="OPENING", received_date=cutoff, received_date_status="declared", initial_quantity=Decimal("10"), unit="kg", source="manual", version=1))
        session.add(InventoryMovementModel(movement_id="po-opening-move", store_id=STORE, lot_id="po-opening", movement_type="opening_balance", quantity_delta=Decimal("10"), unit="kg", occurred_at=datetime(2026, 8, 20, 1, tzinfo=timezone.utc), source="manual"))
        pos = [
            ("po-before", date(2026, 8, 19), "ordered", Decimal("0")),
            ("po-due-open", cutoff, "ordered", Decimal("0")),
            ("po-future", date(2026, 8, 21), "ordered", Decimal("0")),
            ("po-received", cutoff, "received", Decimal("5")),
        ]
        for po_id, delivery, status, received in pos:
            session.add(PurchaseOrderModel(po_id=po_id, store_id=STORE, plan_run_id="po-plan", supplier_id="po-sup", order_date=date(2026, 8, 18), delivery_date=delivery, strategy="test", status=status, total=50, budget_after=0, version=1, received_at=(datetime(2026, 8, 20, 3, tzinfo=timezone.utc) if status == "received" else None)))
            session.add(PurchaseOrderLineModel(po_line_id=f"{po_id}-line", po_id=po_id, ingredient_id="po-ing", ordered_quantity=Decimal("5"), received_quantity=received, unit="kg", unit_cost=10, cost=50, moq=Decimal("1"), pack_size=Decimal("1"), version=1))
        session.flush()
        planner = ProcurementPlanningService(session)
        lots = planner._lots(STORE, "po-ing", cutoff)
        inbound = planner._open_inbound(STORE, cutoff)["po-ing"]
        assert sum(Decimal(x["quantity"]) for x in lots) == Decimal("15")
        assert [(x["lot_id"], Decimal(x["quantity"])) for x in inbound] == [("po:po-future:po-future-line", Decimal("5"))]
        # Adapter consumes the exact same canonical view, so optimizer and
        # exact FEFO receive 15 initial plus one future 5—not duplicated 20.
        from app.services.decision.adapters.procurement_adapter import CoreProcurementAdapter
        assert CoreProcurementAdapter(session).legacy_state._open_inbound(STORE, cutoff) == planner._open_inbound(STORE, cutoff)

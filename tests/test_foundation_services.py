import json

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.exceptions import DuplicateRequestError, StoreNotFoundError
from app.db.unit_of_work import UnitOfWork
from app.models.audit_log import AuditLogModel
from app.models.idempotency import IdempotencyRecordModel
from app.models.store import StoreModel
from app.repositories.stores import StoreRepository
from app.services.audit_service import AuditService
from app.services.idempotency_service import IdempotencyService
from scripts.seed_database import seed_database


def test_seed_is_idempotent_and_does_not_overwrite(session_factory):
    assert seed_database(session_factory) == ["STORE_001", "STORE_TEST_001"]
    with session_factory() as session:
        store = session.get(StoreModel, "STORE_001")
        store.store_name = "Tên người dùng"
        session.commit()

    assert seed_database(session_factory) == []
    with session_factory() as session:
        stores = list(session.scalars(select(StoreModel).order_by(StoreModel.store_id)))
        assert [store.store_id for store in stores] == ["STORE_001", "STORE_TEST_001"]
        assert session.get(StoreModel, "STORE_001").store_name == "Tên người dùng"


def test_store_repository_and_missing_error(session_factory):
    seed_database(session_factory)
    with session_factory() as session:
        repository = StoreRepository(session)
        assert repository.exists("STORE_001")
        assert repository.get_required("STORE_001").store_id == "STORE_001"
        assert repository.get("STORE_TEST_001").store_id == "STORE_TEST_001"
        with pytest.raises(StoreNotFoundError) as exc_info:
            repository.get_required("MISSING")
        assert exc_info.value.code == "STORE_NOT_FOUND"


def test_unit_of_work_commit_and_rollback(session_factory):
    with UnitOfWork(session_factory) as uow:
        uow.stores.add(StoreModel(store_id="COMMIT", store_name="Committed"))
        uow.commit()
    with session_factory() as session:
        assert session.get(StoreModel, "COMMIT") is not None

    with pytest.raises(RuntimeError):
        with UnitOfWork(session_factory) as uow:
            uow.stores.add(StoreModel(store_id="ROLLBACK_A", store_name="A"))
            uow.stores.add(StoreModel(store_id="ROLLBACK_B", store_name="B"))
            raise RuntimeError("stop workflow")
    with session_factory() as session:
        assert session.get(StoreModel, "ROLLBACK_A") is None
        assert session.get(StoreModel, "ROLLBACK_B") is None


def test_unit_of_work_closes_session(session_factory):
    closed_sessions: list[Session] = []

    class TrackingSession(Session):
        def close(self):
            closed_sessions.append(self)
            super().close()

    tracking_factory = sessionmaker(
        bind=session_factory.kw["bind"],
        class_=TrackingSession,
        expire_on_commit=False,
    )
    with UnitOfWork(tracking_factory) as uow:
        uow.stores.add(StoreModel(store_id="CLOSED", store_name="Closed"))
        uow.commit()
    assert len(closed_sessions) == 1


def test_idempotency_replay_conflict_and_store_scope(session_factory):
    seed_database(session_factory)
    with UnitOfWork(session_factory) as uow:
        service = IdempotencyService(uow.idempotency)
        first = service.register(
            store_id="STORE_001",
            endpoint="/resource",
            http_method="post",
            idempotency_key="same-key",
            request_hash="hash-a",
            response_status=201,
            response_body={"ok": True},
        )
        assert not first.is_replay
        uow.commit()

    with UnitOfWork(session_factory) as uow:
        service = IdempotencyService(uow.idempotency)
        replay = service.register(
            store_id="STORE_001",
            endpoint="/resource",
            http_method="POST",
            idempotency_key="same-key",
            request_hash="hash-a",
        )
        assert replay.is_replay
        assert json.loads(replay.record.response_body_json) == {"ok": True}
        with pytest.raises(DuplicateRequestError) as exc_info:
            service.register(
                store_id="STORE_001",
                endpoint="/resource",
                http_method="POST",
                idempotency_key="same-key",
                request_hash="hash-b",
            )
        assert exc_info.value.details["reason"] == "IDEMPOTENCY_KEY_REUSED"

        other_store = service.register(
            store_id="STORE_TEST_001",
            endpoint="/resource",
            http_method="POST",
            idempotency_key="same-key",
            request_hash="hash-b",
        )
        assert not other_store.is_replay
        uow.commit()


def test_idempotency_and_audit_follow_workflow_rollback(session_factory):
    seed_database(session_factory)
    with pytest.raises(RuntimeError):
        with UnitOfWork(session_factory) as uow:
            IdempotencyService(uow.idempotency).register(
                store_id="STORE_001",
                endpoint="/rollback",
                http_method="POST",
                idempotency_key="rollback-key",
                request_hash="hash",
            )
            AuditService(uow.audit_logs).record(
                store_id="STORE_001",
                action="create",
                resource_type="test",
                resource_id="rollback",
                after={"value": 1},
                source="test",
            )
            raise RuntimeError("rollback")
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(IdempotencyRecordModel)) == 0
        assert session.scalar(select(func.count()).select_from(AuditLogModel)) == 0


def test_audit_commit_and_secret_redaction(session_factory):
    seed_database(session_factory)
    with UnitOfWork(session_factory) as uow:
        record = AuditService(uow.audit_logs).record(
            store_id="STORE_001",
            action="update",
            resource_type="store",
            resource_id="STORE_001",
            before={"store_name": "Old"},
            after={"store_name": "New", "api_key": "must-not-appear"},
            source="test",
        )
        audit_id = record.audit_log_id
        uow.commit()
    with session_factory() as session:
        stored = session.get(AuditLogModel, audit_id)
        assert "must-not-appear" not in stored.after_json
        assert "[REDACTED]" in stored.after_json

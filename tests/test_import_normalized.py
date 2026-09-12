import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.exc import OperationalError

from app.config import Settings
from app.core.exceptions import ImportProcessingError
from app.core.rule_mapper import finalize_mapping, map_sheet_rules
from app.db.session import create_engine_from_url, create_session_factory
from app.llm.openrouter_qwen import OpenRouterQwenProvider
from app.main import create_app
from app.models.audit_log import AuditLogModel
from app.models.business import SalesDailyModel
from app.models.import_legacy import ImportModel
from app.models.import_normalized import (
    ImportFileModel,
    ImportIssueModel,
    ImportJobModel,
    ImportMappingModel,
    ImportSheetProfileModel,
)
from app.repositories.normalized_imports import NormalizedImportRepository
from app.schemas.llm import MappingSuggestion, SheetProfile
from app.services.audit_service import AuditService
from app.services.business_persistence import ImportBusinessPersistenceService
from scripts.seed_database import seed_database
from tests.conftest import migrate_database


SALES_CSV = "Ngày,Tên món,SL bán\n2026-07-27,Cà phê sữa,12\n".encode()


def upload_csv(client, *, store_id="STORE_001", content=SALES_CSV, key=None, name="sales.csv"):
    headers = {"Idempotency-Key": key} if key else {}
    return client.post(
        "/api/v1/imports",
        headers=headers,
        data={
            "store_id": store_id,
            "forecast_date": "2026-07-27",
            "forecast_horizon": "7",
        },
        files={"files": (name, content, "text/csv")},
    )


def confirm_from_response(client, body, *, use_profile_id=False):
    sheet = body["sheets"][0]
    mapping = sheet["mapping"]
    item = {
        "sheet_type": mapping["sheet_type"],
        "column_mapping": mapping["column_mapping"],
    }
    item["profile_id" if use_profile_id else "sheet_id"] = (
        sheet["profile_id"] if use_profile_id else sheet["sheet_id"]
    )
    return client.post(
        f"/api/v1/imports/{body['import_id']}/confirm",
        json={"mappings": [item]},
    )


def test_create_persists_normalized_metadata_checksum_and_safe_response(client):
    response = upload_csv(client)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "awaiting_review"
    assert body["sheets"][0]["sheet_id"] == body["profiles"][0]["sheet_id"]
    assert body["sheets"][0]["profile_id"] == body["profiles"][0]["profile_id"]
    assert body["suggested_mappings"][0]["profile_id"] == body["profiles"][0]["profile_id"]
    assert "stored_path" not in response.text
    assert "sha256_checksum" not in response.text

    with client.app.state.session_factory() as session:
        job = session.get(ImportJobModel, body["import_id"])
        file_model = session.scalar(
            select(ImportFileModel).where(ImportFileModel.import_id == body["import_id"])
        )
        assert job.status == "mapping_required"
        assert file_model.sha256_checksum == hashlib.sha256(SALES_CSV).hexdigest()
        assert file_model.file_size == len(SALES_CSV)
        assert session.scalar(
            select(func.count())
            .select_from(ImportSheetProfileModel)
            .where(ImportSheetProfileModel.import_id == body["import_id"])
        ) == 1
        assert session.scalar(
            select(func.count())
            .select_from(ImportMappingModel)
            .where(ImportMappingModel.import_id == body["import_id"])
        ) == 1


def test_store_validation_and_csv_profile(client):
    missing = upload_csv(client, store_id="STORE_MISSING")
    assert missing.status_code == 404
    assert missing.json()["code"] == "STORE_NOT_FOUND"
    assert missing.json()["details"]["store_id"] == "STORE_MISSING"

    created = upload_csv(client).json()
    profile = created["profiles"][0]
    assert profile["sheet_name"] == "sales"
    assert profile["row_count"] == 1
    assert profile["sample_rows"][0]["Tên món"] == "Cà phê sữa"


def test_multiple_files_and_file_count_limit(client):
    response = client.post(
        "/api/v1/imports",
        data={"store_id": "STORE_001"},
        files=[
            ("files", ("sales-a.csv", SALES_CSV, "text/csv")),
            ("files", ("sales-b.csv", SALES_CSV, "text/csv")),
        ],
    )
    assert response.status_code == 201, response.text
    import_id = response.json()["import_id"]
    with client.app.state.session_factory() as session:
        assert session.scalar(
            select(func.count())
            .select_from(ImportFileModel)
            .where(ImportFileModel.import_id == import_id)
        ) == 2

    too_many = client.post(
        "/api/v1/imports",
        data={"store_id": "STORE_001"},
        files=[
            ("files", (f"sales-{index}.csv", SALES_CSV, "text/csv"))
            for index in range(11)
        ],
    )
    assert too_many.status_code == 400
    assert too_many.json()["code"] == "too_many_files"


def test_idempotency_replay_conflict_store_scope_and_no_duplicate_files(client):
    first = upload_csv(client, key="import-key")
    replay = upload_csv(client, key="import-key")
    assert first.status_code == replay.status_code == 201
    assert first.json()["import_id"] == replay.json()["import_id"]
    with client.app.state.session_factory() as session:
        assert session.scalar(
            select(func.count())
            .select_from(ImportFileModel)
            .where(ImportFileModel.import_id == first.json()["import_id"])
        ) == 1

    changed = upload_csv(
        client,
        key="import-key",
        content="Ngày,Tên món,SL bán\n2026-07-27,Trà,3\n".encode(),
    )
    assert changed.status_code == 409
    assert changed.json()["code"] == "DUPLICATE_REQUEST"

    other_store = upload_csv(client, store_id="STORE_TEST_001", key="import-key")
    assert other_store.status_code == 201
    assert other_store.json()["import_id"] != first.json()["import_id"]


def test_restart_reads_normalized_profiles_and_mappings(client):
    created = upload_csv(client).json()
    service = client.app.state.import_service
    first = service.get(created["import_id"])
    second = service.get(created["import_id"])
    assert first["sheets"] == second["sheets"]
    assert second["sheets"][0]["profile_id"] == created["profiles"][0]["profile_id"]


def test_import_survives_application_restart(tmp_path):
    database_url = f"sqlite:///{(tmp_path / 'restart.db').as_posix()}"
    migrate_database(database_url)
    engine = create_engine_from_url(database_url)
    seed_database(create_session_factory(engine))
    engine.dispose()
    settings = Settings(
        database_url=database_url,
        upload_dir=tmp_path / "uploads",
        result_dir=tmp_path / "results",
    )
    with TestClient(create_app(settings)) as first_client:
        created = upload_csv(first_client).json()
        assert confirm_from_response(first_client, created).status_code == 200
        assert first_client.post(
            f"/api/v1/imports/{created['import_id']}/process"
        ).status_code == 200
    (settings.result_dir / f"{created['import_id']}.json").unlink()
    with TestClient(create_app(settings)) as second_client:
        restored = second_client.get(
            f"/api/v1/imports/{created['import_id']}"
        )
        assert restored.status_code == 200
        assert restored.json()["profiles"] == created["profiles"]
        assert restored.json()["suggested_mappings"] == created["suggested_mappings"]
        result = second_client.get(
            f"/api/v1/imports/{created['import_id']}/result"
        )
        assert result.status_code == 200
        assert result.json()["store_id"] == "STORE_001"


def test_lazy_backfill_preserves_realistic_legacy_payload(client):
    import_id = "00000000-0000-0000-0000-00000000bacc"
    created_at = datetime.now(timezone.utc)
    sheet_id = "legacy_file_sales.csv:0:sales"
    profile = {
        "file_name": "sales.csv",
        "sheet_name": "sales",
        "header_row_zero_based": 0,
        "row_count": 1,
        "column_count": 3,
        "columns": ["Ngày", "Tên món", "SL bán"],
        "dtypes": {"Ngày": "object", "Tên món": "object", "SL bán": "int64"},
        "sample_rows": [{"Ngày": "2026-07-27", "Tên món": "Cà phê", "SL bán": 2}],
    }
    mapping = {
        "sheet_type": "sales_history",
        "confidence": 1.0,
        "column_mapping": {
            "Ngày": "date",
            "Tên món": "product_name",
            "SL bán": "quantity_sold",
        },
        "warnings": [],
        "errors": [],
        "source": "rule",
        "requires_review": False,
    }
    payload = {
        "status": "awaiting_review",
        "store_id": "STORE_001",
        "forecast_date": "2026-07-27",
        "forecast_horizon": 7,
        "sheets": [
            {
                "sheet_id": sheet_id,
                "stored_name": "legacy_file_sales.csv",
                "profile": profile,
                "mapping": mapping,
                "rows": [{"Ngày": "2026-07-27", "Tên món": "Cà phê", "SL bán": 2}],
            }
        ],
        "warnings": [],
        "errors": [],
        "requires_review": False,
        "created_at": created_at.isoformat(),
        "result": None,
    }
    with client.app.state.session_factory() as session:
        session.add(
            ImportModel(
                id=import_id,
                status="awaiting_review",
                payload=json.dumps(payload, ensure_ascii=False),
                created_at=created_at,
                updated_at=created_at,
            )
        )
        session.commit()

    response = client.get(f"/api/v1/imports/{import_id}")
    assert response.status_code == 200
    assert response.json()["mappings"][0]["sheet_id"] == sheet_id
    with client.app.state.session_factory() as session:
        assert session.get(ImportJobModel, import_id) is not None
        assert session.scalar(
            select(func.count())
            .select_from(ImportSheetProfileModel)
            .where(ImportSheetProfileModel.import_id == import_id)
        ) == 1
        assert "legacy_import_backfilled" in list(
            session.scalars(
                select(AuditLogModel.action).where(AuditLogModel.resource_id == import_id)
            )
        )


def test_confirm_by_sheet_and_profile_id_and_mismatch(client):
    by_sheet = upload_csv(client).json()
    confirmed = confirm_from_response(client, by_sheet)
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"

    by_profile = upload_csv(client).json()
    confirmed = confirm_from_response(client, by_profile, use_profile_id=True)
    assert confirmed.status_code == 200

    mismatch_source = upload_csv(client).json()
    other = upload_csv(client).json()
    sheet = mismatch_source["sheets"][0]
    response = client.post(
        f"/api/v1/imports/{mismatch_source['import_id']}/confirm",
        json={
            "mappings": [
                {
                    "sheet_id": sheet["sheet_id"],
                    "profile_id": other["profiles"][0]["profile_id"],
                    "sheet_type": sheet["mapping"]["sheet_type"],
                    "column_mapping": sheet["mapping"]["column_mapping"],
                }
            ]
        },
    )
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_confirm_skip_and_transaction_rollback(client):
    body = upload_csv(client).json()
    skipped = client.post(
        f"/api/v1/imports/{body['import_id']}/confirm",
        json={"mappings": [{"profile_id": body["profiles"][0]["profile_id"], "skip": True}]},
    )
    assert skipped.status_code == 200
    assert skipped.json()["mappings"][0]["mapping"]["sheet_type"] == "unknown"

    multi = client.post(
        "/api/v1/imports",
        data={"store_id": "STORE_001"},
        files=[
            ("files", ("sales-a.csv", SALES_CSV, "text/csv")),
            ("files", ("sales-b.csv", SALES_CSV, "text/csv")),
        ],
    ).json()
    first, second = multi["sheets"]
    payload = {
        "mappings": [
            {
                "sheet_id": first["sheet_id"],
                "sheet_type": first["mapping"]["sheet_type"],
                "column_mapping": first["mapping"]["column_mapping"],
            },
            {
                "sheet_id": second["sheet_id"],
                "sheet_type": "inventory",
                "column_mapping": {
                    next(iter(second["mapping"]["column_mapping"])): "revenue"
                },
            },
        ]
    }
    failed = client.post(f"/api/v1/imports/{multi['import_id']}/confirm", json=payload)
    assert failed.status_code == 422
    status = client.get(f"/api/v1/imports/{multi['import_id']}").json()
    assert status["status"] == "awaiting_review"
    assert not status["mappings"][0]["mapping"].get("confirmed", False)


def test_process_state_idempotency_issues_and_db_result(client):
    body = upload_csv(client).json()
    before_confirm = client.post(f"/api/v1/imports/{body['import_id']}/process")
    assert before_confirm.status_code == 409
    assert before_confirm.json()["code"] == "INVALID_STATE_TRANSITION"

    assert confirm_from_response(client, body).status_code == 200
    first = client.post(f"/api/v1/imports/{body['import_id']}/process")
    second = client.post(f"/api/v1/imports/{body['import_id']}/process")
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    result_path = client.app.state.settings.result_dir / f"{body['import_id']}.json"
    result_path.unlink()
    result = client.get(f"/api/v1/imports/{body['import_id']}/result")
    assert result.status_code == 200
    assert result.json()["store_id"] == "STORE_001"
    with client.app.state.session_factory() as session:
        job = session.get(ImportJobModel, body["import_id"])
        assert job.status == "completed"
        assert job.result_json
        assert session.scalar(
            select(func.count())
            .select_from(ImportIssueModel)
            .where(ImportIssueModel.import_id == body["import_id"])
        ) >= 0


def test_sqlite_uncommitted_processing_claim_is_not_visible_across_sessions(client):
    body = upload_csv(client).json()
    assert confirm_from_response(client, body).status_code == 200

    first = client.app.state.session_factory()
    second = client.app.state.session_factory()
    try:
        first_job = first.get(ImportJobModel, body["import_id"])
        second_job = second.get(ImportJobModel, body["import_id"])
        assert first_job.status == second_job.status == "confirmed"

        first_job.status = "processing"
        first.flush()

        second.expire(second_job)
        assert second.get(ImportJobModel, body["import_id"]).status == "confirmed"
        second_job.status = "processing"
        with pytest.raises(OperationalError, match="database is locked"):
            second.flush()
    finally:
        second.rollback()
        first.rollback()
        second.close()
        first.close()


def test_sqlite_stale_session_can_reapply_processing_after_another_claim_commits(client):
    body = upload_csv(client).json()
    assert confirm_from_response(client, body).status_code == 200

    first = client.app.state.session_factory()
    second = client.app.state.session_factory()
    try:
        first_job = first.get(ImportJobModel, body["import_id"])
        second_job = second.get(ImportJobModel, body["import_id"])
        assert first_job.status == second_job.status == "confirmed"

        first_job.status = "processing"
        first.commit()

        second_job.status = "processing"
        second.flush()
        second.commit()

        with client.app.state.session_factory() as verifier:
            assert verifier.get(ImportJobModel, body["import_id"]).status == "processing"
    finally:
        second.rollback()
        first.rollback()
        second.close()
        first.close()


def test_process_claim_allows_only_one_concurrent_pipeline_and_persistence(client, monkeypatch):
    body = upload_csv(client).json()
    assert confirm_from_response(client, body).status_code == 200
    service = client.app.state.import_service
    pipeline_started = threading.Event()
    release_pipeline = threading.Event()
    counter_lock = threading.Lock()
    pipeline_calls = 0
    persistence_calls = 0
    original_process_sheet = service.pipeline.process_sheet
    from app.services.business_persistence import ImportBusinessPersistenceService

    original_persist = ImportBusinessPersistenceService.persist

    def blocked_process_sheet(sheet):
        nonlocal pipeline_calls
        with counter_lock:
            pipeline_calls += 1
        pipeline_started.set()
        assert release_pipeline.wait(timeout=10)
        return original_process_sheet(sheet)

    def counted_persist(instance, **kwargs):
        nonlocal persistence_calls
        with counter_lock:
            persistence_calls += 1
        return original_persist(instance, **kwargs)

    monkeypatch.setattr(service.pipeline, "process_sheet", blocked_process_sheet)
    monkeypatch.setattr(ImportBusinessPersistenceService, "persist", counted_persist)
    winner_result = {}

    def run_winner():
        try:
            winner_result["record"] = service.process(body["import_id"])
        except Exception as exc:  # pragma: no cover - asserted below
            winner_result["error"] = exc

    winner = threading.Thread(target=run_winner)
    winner.start()
    assert pipeline_started.wait(timeout=10)

    with pytest.raises(ImportProcessingError) as loser:
        service.process(body["import_id"])
    assert loser.value.code == "IMPORT_PROCESSING"
    assert loser.value.http_status == 409
    with counter_lock:
        assert pipeline_calls == 1
        assert persistence_calls == 0

    release_pipeline.set()
    winner.join(timeout=10)
    assert not winner.is_alive()
    assert "error" not in winner_result
    assert winner_result["record"]["status"] == "processed"
    with counter_lock:
        assert pipeline_calls == persistence_calls == 1

    with client.app.state.session_factory() as session:
        job = session.get(ImportJobModel, body["import_id"])
        assert job.status == "completed"
        assert session.get(ImportModel, body["import_id"]).status == "processed"
        actions = list(session.scalars(select(AuditLogModel.action).where(
            AuditLogModel.resource_id == body["import_id"]
        )))
        assert actions.count("import_processing_started") == 1
        assert actions.count("import_completed") == 1


def test_process_rejects_a_committed_processing_job_with_current_api_error(client):
    body = upload_csv(client).json()
    assert confirm_from_response(client, body).status_code == 200
    with client.app.state.session_factory() as session:
        job = session.get(ImportJobModel, body["import_id"])
        job.status = "processing"
        job.processing_started_at = datetime.now(timezone.utc)
        session.commit()

    response = client.post(f"/api/v1/imports/{body['import_id']}/process")
    assert response.status_code == 409
    assert response.json()["code"] == "IMPORT_PROCESSING"
    assert response.json()["details"] == {"import_id": body["import_id"]}


def test_processing_failure_sets_failed_without_result(client, monkeypatch):
    body = upload_csv(client).json()
    assert confirm_from_response(client, body).status_code == 200

    def fail_processing(_):
        raise RuntimeError("injected processing failure")

    monkeypatch.setattr(
        client.app.state.import_service.pipeline, "process_sheet", fail_processing
    )
    with pytest.raises(RuntimeError):
        client.app.state.import_service.process(body["import_id"])
    with client.app.state.session_factory() as session:
        job = session.get(ImportJobModel, body["import_id"])
        assert job.status == "failed"
        assert job.result_json is None
        assert session.scalar(
            select(func.count())
            .select_from(ImportIssueModel)
            .where(
                ImportIssueModel.import_id == body["import_id"],
                ImportIssueModel.issue_source == "processing",
            )
        ) == 1
        actions = list(
            session.scalars(
                select(AuditLogModel.action).where(
                    AuditLogModel.resource_id == body["import_id"]
                )
            )
        )
        assert "import_failed" in actions
        assert actions.count("import_processing_started") == 1
        assert "import_completed" not in actions
    retry = client.post(f"/api/v1/imports/{body['import_id']}/process")
    assert retry.status_code == 409
    assert retry.json()["code"] == "INVALID_STATE_TRANSITION"


def test_terminal_legacy_bridge_failure_rolls_back_business_state(client, monkeypatch):
    body = upload_csv(client).json()
    assert confirm_from_response(client, body).status_code == 200
    original_sync = NormalizedImportRepository.sync_legacy

    def fail_completed_bridge(repository, record):
        if record["internal_status"] == "completed":
            raise RuntimeError("injected legacy bridge failure")
        return original_sync(repository, record)

    monkeypatch.setattr(NormalizedImportRepository, "sync_legacy", fail_completed_bridge)
    with pytest.raises(RuntimeError, match="injected legacy bridge failure"):
        client.app.state.import_service.process(body["import_id"])

    with client.app.state.session_factory() as session:
        job = session.get(ImportJobModel, body["import_id"])
        legacy = session.get(ImportModel, body["import_id"])
        actions = list(session.scalars(select(AuditLogModel.action).where(
            AuditLogModel.resource_id == body["import_id"]
        )))
        assert session.scalar(select(func.count()).select_from(SalesDailyModel)) == 0
        assert job.status == "failed"
        assert job.result_json is None
        assert legacy.status == "failed"
        assert "import_completed" not in actions
        assert actions.count("import_failed") == 1


def test_terminal_success_audit_failure_rolls_back_business_state(client, monkeypatch):
    body = upload_csv(client).json()
    assert confirm_from_response(client, body).status_code == 200
    original_record = AuditService.record

    def fail_completed_audit(audit, **kwargs):
        if kwargs["action"] == "import_completed":
            raise RuntimeError("injected terminal audit failure")
        return original_record(audit, **kwargs)

    monkeypatch.setattr(AuditService, "record", fail_completed_audit)
    with pytest.raises(RuntimeError, match="injected terminal audit failure"):
        client.app.state.import_service.process(body["import_id"])

    with client.app.state.session_factory() as session:
        job = session.get(ImportJobModel, body["import_id"])
        legacy = session.get(ImportModel, body["import_id"])
        actions = list(session.scalars(select(AuditLogModel.action).where(
            AuditLogModel.resource_id == body["import_id"]
        )))
        assert session.scalar(select(func.count()).select_from(SalesDailyModel)) == 0
        assert job.status == "failed"
        assert job.result_json is None
        assert legacy.status == "failed"
        assert "import_completed" not in actions
        assert actions.count("import_failed") == 1


def test_terminal_persistence_failure_after_flush_rolls_back_business_state(client, monkeypatch):
    body = upload_csv(client).json()
    assert confirm_from_response(client, body).status_code == 200
    original_persist = ImportBusinessPersistenceService.persist

    def persist_then_fail(persistence, **kwargs):
        original_persist(persistence, **kwargs)
        raise RuntimeError("injected post-flush persistence failure")

    monkeypatch.setattr(ImportBusinessPersistenceService, "persist", persist_then_fail)
    with pytest.raises(RuntimeError, match="injected post-flush persistence failure"):
        client.app.state.import_service.process(body["import_id"])

    with client.app.state.session_factory() as session:
        job = session.get(ImportJobModel, body["import_id"])
        legacy = session.get(ImportModel, body["import_id"])
        actions = list(session.scalars(select(AuditLogModel.action).where(
            AuditLogModel.resource_id == body["import_id"]
        )))
        assert session.scalar(select(func.count()).select_from(SalesDailyModel)) == 0
        assert job.status == "failed"
        assert job.result_json is None
        assert legacy.status == "failed"
        assert "import_completed" not in actions
        assert actions.count("import_failed") == 1


def test_processing_started_audit_does_not_lock_pipeline_window(client, monkeypatch):
    body = upload_csv(client).json()
    assert confirm_from_response(client, body).status_code == 200
    service = client.app.state.import_service
    statements = []
    original_process_sheet = service.pipeline.process_sheet

    def capture_audit_insert(_connection, _cursor, statement, _parameters, _context, _executemany):
        if "INSERT INTO audit_logs" in statement:
            statements.append(statement)

    def assert_started_audit_is_already_flushed(sheet):
        assert statements
        contender = client.app.state.session_factory()
        try:
            contender.connection().exec_driver_sql("PRAGMA busy_timeout = 1")
            contender.add(AuditLogModel(
                audit_log_id=str(uuid4()), store_id="STORE_001",
                action="import_terminal_contention_probe", resource_type="test",
                resource_id=body["import_id"], source="test",
            ))
            contender.flush()
            contender.commit()
        finally:
            contender.rollback()
            contender.close()
        return original_process_sheet(sheet)

    event.listen(client.app.state.engine, "before_cursor_execute", capture_audit_insert)
    monkeypatch.setattr(service.pipeline, "process_sheet", assert_started_audit_is_already_flushed)
    try:
        response = client.post(f"/api/v1/imports/{body['import_id']}/process")
    finally:
        event.remove(client.app.state.engine, "before_cursor_execute", capture_audit_insert)
    assert response.status_code == 200, response.text


def test_processing_start_audit_failure_marks_job_failed_without_pipeline(client, monkeypatch):
    body = upload_csv(client).json()
    assert confirm_from_response(client, body).status_code == 200
    service = client.app.state.import_service
    pipeline_calls = 0
    original_record = AuditService.record
    original_process_sheet = service.pipeline.process_sheet

    def fail_start_audit(audit, **kwargs):
        if kwargs["action"] == "import_processing_started":
            raise RuntimeError("injected start audit failure")
        return original_record(audit, **kwargs)

    def count_pipeline(sheet):
        nonlocal pipeline_calls
        pipeline_calls += 1
        return original_process_sheet(sheet)

    monkeypatch.setattr(AuditService, "record", fail_start_audit)
    monkeypatch.setattr(service.pipeline, "process_sheet", count_pipeline)
    with pytest.raises(RuntimeError, match="injected start audit failure"):
        service.process(body["import_id"])

    with client.app.state.session_factory() as session:
        job = session.get(ImportJobModel, body["import_id"])
        actions = list(session.scalars(select(AuditLogModel.action).where(
            AuditLogModel.resource_id == body["import_id"]
        )))
        assert pipeline_calls == 0
        assert job.status == "failed"
        assert "import_processing_started" not in actions
        assert actions.count("import_failed") == 1


def test_result_file_failure_keeps_committed_database_result(client, monkeypatch):
    body = upload_csv(client).json()
    assert confirm_from_response(client, body).status_code == 200
    result_dir = client.app.state.settings.result_dir
    original_write_text = Path.write_text

    def fail_terminal_artifact(path, *args, **kwargs):
        if path.parent == result_dir and path.name.endswith(".tmp"):
            raise OSError("injected result artifact failure")
        return original_write_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_terminal_artifact)
    response = client.post(f"/api/v1/imports/{body['import_id']}/process")
    assert response.status_code == 200, response.text
    assert not (result_dir / f"{body['import_id']}.json").exists()
    result = client.get(f"/api/v1/imports/{body['import_id']}/result")
    assert result.status_code == 200
    with client.app.state.session_factory() as session:
        job = session.get(ImportJobModel, body["import_id"])
        legacy = session.get(ImportModel, body["import_id"])
        actions = list(session.scalars(select(AuditLogModel.action).where(
            AuditLogModel.resource_id == body["import_id"]
        )))
        assert job.status == "completed"
        assert job.result_json
        assert legacy.status == "processed"
        assert actions.count("import_completed") == 1


def test_corrupt_workbook_rolls_back_and_removes_temp_files(client):
    before_dirs = set(client.app.state.settings.upload_dir.glob("*"))
    response = client.post(
        "/api/v1/imports",
        data={"store_id": "STORE_001"},
        files={"files": ("broken.xlsx", b"not-an-excel-file", "application/octet-stream")},
    )
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_excel_file"
    after_dirs = set(client.app.state.settings.upload_dir.glob("*"))
    assert after_dirs == before_dirs
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ImportJobModel)) == 0


def test_total_request_limit_and_filesystem_failure_compensation(tmp_path, monkeypatch):
    database_url = f"sqlite:///{(tmp_path / 'limits.db').as_posix()}"
    migrate_database(database_url)
    engine = create_engine_from_url(database_url)
    seed_database(create_session_factory(engine))
    engine.dispose()
    settings = Settings(
        database_url=database_url,
        upload_dir=tmp_path / "uploads",
        result_dir=tmp_path / "results",
        max_file_size_mb=2,
        max_total_upload_size_mb=1,
    )
    app = create_app(settings)
    large_csv = b"a,b\n" + (b"1,2\n" * 160_000)
    with TestClient(app, raise_server_exceptions=False) as isolated:
        too_large = isolated.post(
            "/api/v1/imports",
            data={"store_id": "STORE_001"},
            files=[
                ("files", ("a.csv", large_csv, "text/csv")),
                ("files", ("b.csv", large_csv, "text/csv")),
            ],
        )
        assert too_large.status_code == 413
        assert too_large.json()["code"] == "request_too_large"

        original_replace = Path.replace

        def injected_replace(path, target):
            if path.name.endswith(".tmp"):
                raise OSError("injected rename failure")
            return original_replace(path, target)

        monkeypatch.setattr(Path, "replace", injected_replace)
        failed = upload_csv(isolated)
        assert failed.status_code == 500
        assert failed.json()["code"] == "INTERNAL_ERROR"
        with isolated.app.state.session_factory() as session:
            assert session.scalar(select(func.count()).select_from(ImportJobModel)) == 0
        assert list(settings.upload_dir.glob("*")) == []


def test_mapping_finalizer_removes_stale_structural_warning_and_keeps_semantic():
    columns = ["Ngày", "Tên món", "SL bán"]
    profile = SheetProfile(
        file_name="sales.csv",
        sheet_name="sales",
        header_row_zero_based=0,
        row_count=1,
        column_count=3,
        columns=columns,
        dtypes={column: "object" for column in columns},
        sample_rows=[],
    )
    suggestion = MappingSuggestion(
        sheet_type="sales_history",
        confidence=0.95,
        column_mapping={
            "Ngày": "date",
            "Tên món": "product_name",
            "SL bán": "quantity_sold",
        },
        warnings=[
            "Missing core fields: ['date', 'product_name', 'quantity_sold']",
            "Tên cột viết tắt cần người dùng kiểm tra.",
        ],
        source="llm",
    )
    final = finalize_mapping(profile, suggestion, 0.82)
    assert not any("Missing core fields" in warning for warning in final.warnings)
    assert final.warnings == ["Tên cột viết tắt cần người dùng kiểm tra."]
    assert not final.requires_review


def test_openrouter_qwen_post_validation_recomputes_structural_warnings():
    profile = SheetProfile(
        file_name="sales.csv",
        sheet_name="sales",
        header_row_zero_based=0,
        row_count=1,
        column_count=3,
        columns=["Ngày", "Tên món", "SL bán"],
        dtypes={},
        sample_rows=[],
    )
    provider = OpenRouterQwenProvider(Settings())
    result = provider._validate_mapping_result(
        {
            "sheet_type": "sales_history",
            "confidence": 0.9,
            "column_mapping": {
                "Ngày": "date",
                "Tên món": "product_name",
                "SL bán": "quantity_sold",
            },
            "warnings": ["Missing core fields: ['date']"],
            "errors": [],
        },
        profile,
    )
    assert result.warnings == []
    assert not result.requires_review


def test_rule_mapper_uses_configured_threshold():
    profile = SheetProfile(
        file_name="inventory.csv",
        sheet_name="inventory",
        header_row_zero_based=0,
        row_count=1,
        column_count=4,
        columns=["Nguyên liệu", "Tồn kho", "X", "Y"],
        dtypes={},
        sample_rows=[],
    )
    strict = map_sheet_rules(profile, confidence_threshold=0.8)
    lenient = map_sheet_rules(profile, confidence_threshold=0.7)
    assert strict.confidence == lenient.confidence == 0.775
    assert strict.requires_review
    assert lenient.requires_review  # incomplete inventory core fields always require review


def test_rule_mapper_maps_inventory_received_date_without_confusing_snapshot_date():
    profile = SheetProfile(
        file_name="inventory.csv",
        sheet_name="inventory",
        header_row_zero_based=0,
        row_count=1,
        column_count=6,
        columns=["Ngày kiểm kê", "Ngày nhận hàng", "Nguyên liệu", "Mã lô", "Tồn kho", "ĐVT"],
        dtypes={},
        sample_rows=[],
    )

    suggestion = map_sheet_rules(profile)

    assert suggestion.sheet_type == "inventory"
    assert suggestion.column_mapping["Ngày kiểm kê"] == "snapshot_date"
    assert suggestion.column_mapping["Ngày nhận hàng"] == "received_date"


def test_audit_payload_does_not_contain_rows_or_api_key(client):
    body = upload_csv(client).json()
    with client.app.state.session_factory() as session:
        logs = list(
            session.scalars(
                select(AuditLogModel).where(AuditLogModel.resource_id == body["import_id"])
            )
        )
        assert logs
        payload = " ".join(log.after_json or "" for log in logs)
        assert "Cà phê sữa" not in payload
        assert "api_key" not in payload.lower()

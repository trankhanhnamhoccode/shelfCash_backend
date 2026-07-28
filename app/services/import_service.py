import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy.exc import IntegrityError

from app.core.canonical_schemas import CANONICAL_SCHEMAS
from app.core.exceptions import (
    DuplicateRequestError,
    ImportNotFoundError,
    ImportNotReadyError,
    ImportProcessingError,
    InvalidStateTransitionError,
    MappingIncompleteError,
    ValidationError,
)
from app.core.excel_reader import (
    ALLOWED_EXTENSIONS,
    ExcelIngestionError,
    read_workbook,
    sanitize_filename,
)
from app.models.import_normalized import (
    ImportFileModel,
    ImportJobModel,
    ImportMappingModel,
    ImportSheetProfileModel,
)
from app.models.store import StoreModel
from app.repositories.audit_logs import AuditLogRepository
from app.repositories.idempotency import IdempotencyRepository
from app.repositories.normalized_imports import (
    INTERNAL_STATUS,
    NormalizedImportRepository,
    json_dump,
    json_load,
)
from app.repositories.stores import StoreRepository
from app.schemas.canonical import CanonicalResult
from app.schemas.llm import MappingSuggestion, SheetProfile
from app.services.audit_service import AuditService
from app.services.idempotency_service import IdempotencyService
from app.services.business_persistence import ImportBusinessPersistenceService, SCHEMA_VERSION


@dataclass
class PreparedUpload:
    original_name: str
    content_type: str | None
    content: bytes
    checksum: str


class ImportService:
    def __init__(self, session_factory, pipeline, settings):
        self.session_factory = session_factory
        self.pipeline = pipeline
        self.settings = settings

    async def create_import(
        self,
        uploads,
        store_id: str,
        forecast_date: date | None,
        forecast_horizon: int,
        idempotency_key: str | None = None,
    ):
        prepared = await self._read_uploads(uploads)
        request_hash = self._request_hash(
            store_id, forecast_date, forecast_horizon, prepared
        )
        session = self.session_factory()
        temp_paths: list[Path] = []
        final_paths: list[Path] = []
        import_dir: Path | None = None
        try:
            StoreRepository(session).get_required(store_id)
            if idempotency_key:
                replay = self._existing_replay(
                    session, store_id, idempotency_key, request_hash
                )
                if replay is not None:
                    session.rollback()
                    return replay

            import_id = str(uuid4())
            import_dir = self.settings.upload_dir / import_id
            import_dir.mkdir(parents=True, exist_ok=True)
            parsed_files: list[tuple[PreparedUpload, str, Path, list]] = []
            for upload in prepared:
                stored_name = f"{uuid4().hex}_{upload.original_name}"
                final_path = import_dir / stored_name
                temp_path = import_dir / f".{stored_name}.{uuid4().hex}.tmp"
                temp_path.write_bytes(upload.content)
                temp_paths.append(temp_path)
                parsed = read_workbook(
                    upload.content,
                    upload.original_name,
                    self.settings.max_sheets_per_file,
                    self.settings.max_rows_per_sheet,
                    self.settings.sample_rows_per_sheet,
                )
                parsed_files.append((upload, stored_name, final_path, parsed))

            repository = NormalizedImportRepository(session)
            job = ImportJobModel(
                import_id=import_id,
                store_id=store_id,
                forecast_date=forecast_date,
                forecast_horizon=forecast_horizon,
                status="mapping_required",
                legacy_status="awaiting_review",
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                requires_review=False,
                created_at=datetime.now(timezone.utc),
            )
            repository.add_job(job)
            for upload, stored_name, final_path, parsed_sheets in parsed_files:
                file_id = str(uuid4())
                repository.add_file(
                    ImportFileModel(
                        import_file_id=file_id,
                        import_id=import_id,
                        original_file_name=upload.original_name,
                        stored_file_name=stored_name,
                        stored_path=str(final_path),
                        content_type=upload.content_type,
                        file_size=len(upload.content),
                        sha256_checksum=upload.checksum,
                        sheet_count=len(parsed_sheets),
                    )
                )
                for parsed in parsed_sheets:
                    profile_id = str(uuid4())
                    compatibility_sheet_id = f"{stored_name}:{parsed.sheet_id}"
                    suggestion = await self.pipeline.suggest(parsed.profile)
                    repository.add_profile(
                        ImportSheetProfileModel(
                            profile_id=profile_id,
                            import_id=import_id,
                            import_file_id=file_id,
                            compatibility_sheet_id=compatibility_sheet_id,
                            sheet_name=parsed.profile.sheet_name,
                            header_row_zero_based=parsed.profile.header_row_zero_based,
                            row_count=parsed.profile.row_count,
                            column_count=parsed.profile.column_count,
                            columns_json=json_dump(parsed.profile.columns),
                            dtypes_json=json_dump(parsed.profile.dtypes),
                            sample_rows_json=json_dump(parsed.profile.sample_rows),
                            parsed_rows_json=json_dump(parsed.rows),
                        )
                    )
                    repository.add_mapping(
                        self._mapping_model(
                            import_id, profile_id, suggestion, confirmed=False
                        )
                    )
                    repository.replace_issues(
                        import_id=import_id,
                        profile_id=profile_id,
                        issue_source=(
                            "llm_mapping"
                            if suggestion.source == "llm"
                            else "rule_mapping"
                        ),
                        warnings=suggestion.warnings,
                        errors=suggestion.errors,
                    )
                    job.requires_review = job.requires_review or suggestion.requires_review

            if idempotency_key:
                IdempotencyService(IdempotencyRepository(session)).register(
                    store_id=store_id,
                    endpoint="/api/v1/imports",
                    http_method="POST",
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    resource_type="import",
                    resource_id=import_id,
                    response_status=201,
                )

            session.flush()
            record = repository.to_record(job)
            repository.sync_legacy(record)
            self._audit(
                session,
                action="import_created",
                record=record,
                file_count=len(prepared),
            )
            session.flush()

            for temp_path, (_, _, final_path, _) in zip(temp_paths, parsed_files):
                temp_path.replace(final_path)
                final_paths.append(final_path)
            session.commit()
            return record
        except IntegrityError:
            session.rollback()
            self._cleanup_files([*temp_paths, *final_paths], import_dir)
            if idempotency_key:
                with self.session_factory() as replay_session:
                    replay = self._existing_replay(
                        replay_session, store_id, idempotency_key, request_hash
                    )
                    if replay is not None:
                        return replay
                raise DuplicateRequestError()
            raise
        except Exception:
            session.rollback()
            self._cleanup_files([*temp_paths, *final_paths], import_dir)
            raise
        finally:
            session.close()

    def get(self, import_id):
        with self.session_factory() as session:
            repository = NormalizedImportRepository(session)
            job = self._require_job(repository, str(import_id))
            session.commit()
            return repository.to_record(job)

    def confirm(self, import_id, confirmed):
        import_id = str(import_id)
        with self.session_factory() as session:
            try:
                repository = NormalizedImportRepository(session)
                job = self._require_job(repository, import_id)
                if job.status not in {"mapping_required", "confirmed"}:
                    raise InvalidStateTransitionError(
                        "Import không thể xác nhận mapping ở trạng thái hiện tại.",
                        {
                            "current_status": job.status,
                            "allowed_statuses": ["mapping_required", "confirmed"],
                        },
                    )
                for request in confirmed:
                    profile = repository.profile_by_reference(
                        import_id,
                        profile_id=str(request.profile_id) if request.profile_id else None,
                        sheet_id=request.sheet_id,
                    )
                    if profile is None:
                        raise ValidationError(
                            "sheet_id/profile_id không tham chiếu cùng một profile.",
                            {
                                "sheet_id": request.sheet_id,
                                "profile_id": str(request.profile_id)
                                if request.profile_id
                                else None,
                            },
                        )
                    mapping = repository.mapping_for_profile(profile.profile_id)
                    if mapping is None:
                        raise MappingIncompleteError(details={"profile_id": profile.profile_id})
                    profile_schema = self._profile_schema(repository, profile)
                    if request.skip:
                        suggestion = MappingSuggestion(
                            sheet_type="unknown",
                            confidence=1.0,
                            column_mapping={
                                column: None for column in profile_schema.columns
                            },
                            warnings=[],
                            errors=[],
                            source="rule",
                            requires_review=False,
                        )
                    else:
                        try:
                            suggestion = self.pipeline.confirm(
                                profile_schema,
                                request.sheet_type,
                                request.column_mapping,
                            )
                        except ValueError as exc:
                            raise ValidationError(
                                "Mapping không hợp lệ.",
                                {"reason": str(exc), "profile_id": profile.profile_id},
                            ) from exc
                    self._update_mapping(mapping, suggestion, confirmed=True)
                    repository.replace_issues(
                        import_id=import_id,
                        profile_id=profile.profile_id,
                        issue_source="mapping_validation",
                        warnings=suggestion.warnings,
                        errors=suggestion.errors,
                    )

                job.status = "confirmed"
                job.legacy_status = "confirmed"
                job.confirmed_at = datetime.now(timezone.utc)
                job.requires_review = any(
                    mapping.requires_review for mapping in repository.mappings(import_id)
                )
                session.flush()
                record = repository.to_record(job)
                repository.sync_legacy(record)
                self._audit(
                    session,
                    action="import_mapping_confirmed",
                    record=record,
                    file_count=len(repository.files(import_id)),
                )
                session.commit()
                return record
            except Exception:
                session.rollback()
                raise

    def process(self, import_id):
        import_id = str(import_id)
        result_path = self.settings.result_dir / f"{import_id}.json"
        temp_path = self.settings.result_dir / f".{import_id}.{uuid4().hex}.tmp"
        with self.session_factory() as session:
            repository = NormalizedImportRepository(session)
            job = self._require_job(repository, import_id)
            if job.status == "completed" and job.result_json and job.business_persisted_at:
                session.commit()
                return repository.to_record(job)
            if job.status == "processing":
                raise ImportProcessingError(details={"import_id": import_id})
            if job.status not in {"confirmed", "completed"}:
                raise InvalidStateTransitionError(
                    "Import phải được xác nhận mapping trước khi xử lý.",
                    {
                        "current_status": job.status,
                        "required_status": "confirmed",
                    },
                )
            try:
                job.status = "processing"
                job.processing_started_at = datetime.now(timezone.utc)
                session.flush()
                record = repository.to_record(job)
                self._audit(
                    session,
                    action="import_processing_started",
                    record=record,
                    file_count=len(repository.files(import_id)),
                )

                canonical_data = {
                    kind: [] for kind in CANONICAL_SCHEMAS if kind != "unknown"
                }
                summary: dict[str, Any] = {}
                business_sheets: list[dict[str, Any]] = []
                profiles_by_id = {
                    profile.profile_id: profile
                    for profile in repository.profiles(import_id)
                }
                for mapping in repository.mappings(import_id):
                    profile = profiles_by_id[mapping.profile_id]
                    sheet = self._pipeline_sheet(repository, profile, mapping)
                    kind, rows, validation = self.pipeline.process_sheet(sheet)
                    if validation["errors"]:
                        repository.replace_issues(
                            import_id=import_id,
                            profile_id=profile.profile_id,
                            issue_source="business_persistence",
                            row_errors=validation["errors"],
                        )
                        raise ValidationError(
                            "Import chứa row không hợp lệ; không ghi business data.",
                            {"profile_id": profile.profile_id, "sheet_id": profile.compatibility_sheet_id},
                        )
                    if kind != "unknown":
                        canonical_data[kind].extend(rows)
                    if mapping.confirmed:
                        business_sheets.append(
                            {
                                "sheet_type": kind,
                                "profile_id": profile.profile_id,
                                "sheet_id": profile.compatibility_sheet_id,
                                "rows": rows,
                            }
                        )
                    summary[profile.compatibility_sheet_id] = validation
                    repository.replace_issues(
                        import_id=import_id,
                        profile_id=profile.profile_id,
                        issue_source="row_validation",
                        row_errors=validation["errors"],
                    )

                canonical = CanonicalResult(
                    store_id=job.store_id,
                    forecast_date=job.forecast_date.isoformat()
                    if job.forecast_date
                    else None,
                    forecast_horizon=job.forecast_horizon,
                    **canonical_data,
                    validation_summary=summary,
                    ingestion_metadata={
                        "import_id": import_id,
                        "processed_at": datetime.now(timezone.utc).isoformat(),
                        "sheet_count": len(profiles_by_id),
                    },
                ).model_dump(mode="json")
                business_summary = ImportBusinessPersistenceService(session).persist(
                    job=job, sheets=business_sheets
                )
                job.result_json = json_dump(canonical)
                job.validation_summary_json = json_dump(summary)
                job.business_write_summary_json = json_dump(business_summary)
                job.business_schema_version = SCHEMA_VERSION
                job.business_persisted_at = datetime.now(timezone.utc)
                job.status = "completed"
                job.legacy_status = "processed"
                job.completed_at = datetime.now(timezone.utc)
                job.failure_code = None
                job.failure_message = None
                session.flush()
                record = repository.to_record(job)
                repository.sync_legacy(record)
                self._audit(
                    session,
                    action="import_completed",
                    record=record,
                    file_count=len(repository.files(import_id)),
                    business_summary=business_summary,
                )
                session.commit()
                try:
                    temp_path.write_text(
                        json.dumps(canonical, ensure_ascii=False), encoding="utf-8"
                    )
                    temp_path.replace(result_path)
                except OSError:
                    temp_path.unlink(missing_ok=True)
                return record
            except Exception as exc:
                session.rollback()
                temp_path.unlink(missing_ok=True)
                result_path.unlink(missing_ok=True)
                self._mark_failed(import_id, exc)
                raise

    def result(self, import_id):
        record = self.get(import_id)
        if record.get("result") is None:
            raise ImportNotReadyError(details={"import_id": str(import_id)})
        return record["result"]

    async def _read_uploads(self, uploads) -> list[PreparedUpload]:
        if len(uploads) > self.settings.max_files_per_request:
            raise ExcelIngestionError(
                "too_many_files",
                f"Maximum {self.settings.max_files_per_request} files per request",
            )
        prepared: list[PreparedUpload] = []
        total_size = 0
        per_file_limit = self.settings.max_file_size_mb * 1024 * 1024
        total_limit = self.settings.max_total_upload_size_mb * 1024 * 1024
        for upload in uploads:
            safe_name = sanitize_filename(upload.filename)
            extension = Path(safe_name).suffix.lower()
            if extension not in ALLOWED_EXTENSIONS:
                raise ExcelIngestionError(
                    "invalid_file_extension",
                    f"Unsupported Excel extension: {extension}",
                )
            content = await upload.read(per_file_limit + 1)
            if len(content) > per_file_limit:
                raise ExcelIngestionError(
                    "file_too_large",
                    f"File '{safe_name}' exceeds {self.settings.max_file_size_mb} MB",
                    status_code=413,
                )
            total_size += len(content)
            if total_size > total_limit:
                raise ExcelIngestionError(
                    "request_too_large",
                    f"Upload request exceeds {self.settings.max_total_upload_size_mb} MB",
                    status_code=413,
                )
            prepared.append(
                PreparedUpload(
                    original_name=safe_name,
                    content_type=upload.content_type,
                    content=content,
                    checksum=hashlib.sha256(content).hexdigest(),
                )
            )
        return prepared

    @staticmethod
    def _request_hash(
        store_id: str,
        forecast_date: date | None,
        forecast_horizon: int,
        uploads: list[PreparedUpload],
    ) -> str:
        value = {
            "store_id": store_id,
            "forecast_date": forecast_date.isoformat() if forecast_date else None,
            "forecast_horizon": forecast_horizon,
            "files": [
                {"file_name": upload.original_name, "checksum": upload.checksum}
                for upload in uploads
            ],
        }
        return hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def _existing_replay(
        self,
        session,
        store_id: str,
        idempotency_key: str,
        request_hash: str,
    ) -> dict[str, Any] | None:
        existing = IdempotencyRepository(session).get(
            store_id=store_id,
            endpoint="/api/v1/imports",
            http_method="POST",
            idempotency_key=idempotency_key,
        )
        if existing is None:
            return None
        if existing.request_hash != request_hash:
            raise DuplicateRequestError(
                "Idempotency-Key đã được sử dụng cho request khác.",
                {},
            )
        if not existing.resource_id:
            raise ImportNotReadyError(details={"idempotency_key": idempotency_key})
        repository = NormalizedImportRepository(session)
        job = self._require_job(repository, existing.resource_id)
        return repository.to_record(job)

    def _require_job(
        self, repository: NormalizedImportRepository, import_id: str
    ) -> ImportJobModel:
        job = repository.get_job(import_id)
        if job is not None:
            return job
        legacy = repository.get_legacy(import_id)
        if legacy is None:
            raise ImportNotFoundError(details={"import_id": import_id})
        return self._backfill_legacy(repository, legacy)

    def _backfill_legacy(self, repository, legacy) -> ImportJobModel:
        payload = json.loads(legacy.payload)
        store_id = payload.get("store_id") or "LEGACY_UNKNOWN"
        stores = StoreRepository(repository.session)
        if not stores.exists(store_id):
            stores.add(
                StoreModel(
                    store_id=store_id,
                    store_name=f"Legacy store {store_id}",
                )
            )
        forecast_date = payload.get("forecast_date")
        internal_status = INTERNAL_STATUS.get(
            legacy.status, INTERNAL_STATUS.get(payload.get("status"), "mapping_required")
        )
        job = ImportJobModel(
            import_id=legacy.id,
            store_id=store_id,
            forecast_date=date.fromisoformat(forecast_date) if forecast_date else None,
            forecast_horizon=int(payload.get("forecast_horizon") or 7),
            status=internal_status,
            legacy_status=legacy.status,
            requires_review=bool(payload.get("requires_review", False)),
            created_at=legacy.created_at,
            confirmed_at=legacy.updated_at if internal_status in {"confirmed", "completed"} else None,
            completed_at=legacy.updated_at if internal_status == "completed" else None,
            result_json=json_dump(payload["result"]) if payload.get("result") is not None else None,
            validation_summary_json=json_dump(
                (payload.get("result") or {}).get("validation_summary", {})
            ),
        )
        repository.add_job(job)
        files: dict[str, str] = {}
        for sheet in payload.get("sheets", []):
            profile_data = sheet["profile"]
            stored_name = sheet.get("stored_name") or profile_data["file_name"]
            if stored_name not in files:
                file_id = str(uuid4())
                stored_path = self.settings.upload_dir / legacy.id / stored_name
                content = stored_path.read_bytes() if stored_path.exists() else b""
                repository.add_file(
                    ImportFileModel(
                        import_file_id=file_id,
                        import_id=legacy.id,
                        original_file_name=profile_data["file_name"],
                        stored_file_name=stored_name,
                        stored_path=str(stored_path),
                        content_type=None,
                        file_size=len(content),
                        sha256_checksum=hashlib.sha256(content).hexdigest(),
                        sheet_count=None,
                        created_at=legacy.created_at,
                    )
                )
                files[stored_name] = file_id
            profile_id = str(uuid4())
            repository.add_profile(
                ImportSheetProfileModel(
                    profile_id=profile_id,
                    import_id=legacy.id,
                    import_file_id=files[stored_name],
                    compatibility_sheet_id=sheet["sheet_id"],
                    sheet_name=profile_data["sheet_name"],
                    header_row_zero_based=profile_data["header_row_zero_based"],
                    row_count=profile_data["row_count"],
                    column_count=profile_data["column_count"],
                    columns_json=json_dump(profile_data["columns"]),
                    dtypes_json=json_dump(profile_data["dtypes"]),
                    sample_rows_json=json_dump(profile_data.get("sample_rows", [])),
                    parsed_rows_json=json_dump(sheet.get("rows", [])),
                    created_at=legacy.created_at,
                )
            )
            suggestion = MappingSuggestion.model_validate(sheet["mapping"])
            repository.add_mapping(
                self._mapping_model(
                    legacy.id,
                    profile_id,
                    suggestion,
                    confirmed=internal_status in {"confirmed", "completed"},
                )
            )
            repository.replace_issues(
                import_id=legacy.id,
                profile_id=profile_id,
                issue_source="legacy_backfill",
                warnings=suggestion.warnings,
                errors=suggestion.errors,
            )
        repository.session.flush()
        record = repository.to_record(job)
        repository.sync_legacy(record)
        self._audit(
            repository.session,
            action="legacy_import_backfilled",
            record=record,
            file_count=len(files),
        )
        return job

    @staticmethod
    def _mapping_model(
        import_id: str,
        profile_id: str,
        suggestion: MappingSuggestion,
        *,
        confirmed: bool,
    ) -> ImportMappingModel:
        now = datetime.now(timezone.utc)
        return ImportMappingModel(
            import_mapping_id=str(uuid4()),
            import_id=import_id,
            profile_id=profile_id,
            sheet_type=suggestion.sheet_type,
            column_mapping_json=json_dump(suggestion.column_mapping),
            confidence=suggestion.confidence,
            source=suggestion.source,
            requires_review=suggestion.requires_review,
            confirmed=confirmed,
            warnings_json=json_dump(suggestion.warnings),
            errors_json=json_dump(suggestion.errors),
            created_at=now,
            updated_at=now,
        )

    @staticmethod
    def _update_mapping(mapping, suggestion, *, confirmed: bool) -> None:
        mapping.sheet_type = suggestion.sheet_type
        mapping.column_mapping_json = json_dump(suggestion.column_mapping)
        mapping.confidence = suggestion.confidence
        mapping.source = suggestion.source
        mapping.requires_review = suggestion.requires_review
        mapping.confirmed = confirmed
        mapping.warnings_json = json_dump(suggestion.warnings)
        mapping.errors_json = json_dump(suggestion.errors)
        mapping.updated_at = datetime.now(timezone.utc)

    @staticmethod
    def _profile_schema(repository, profile) -> SheetProfile:
        file_model = next(
            item
            for item in repository.files(profile.import_id)
            if item.import_file_id == profile.import_file_id
        )
        return SheetProfile(
            file_name=file_model.original_file_name,
            sheet_name=profile.sheet_name,
            header_row_zero_based=profile.header_row_zero_based,
            row_count=profile.row_count,
            column_count=profile.column_count,
            columns=json_load(profile.columns_json, []),
            dtypes=json_load(profile.dtypes_json, {}),
            sample_rows=json_load(profile.sample_rows_json, []),
        )

    def _pipeline_sheet(self, repository, profile, mapping) -> dict[str, Any]:
        profile_schema = self._profile_schema(repository, profile)
        return {
            "profile": profile_schema.model_dump(mode="json"),
            "rows": json_load(profile.parsed_rows_json, []),
            "mapping": {
                "sheet_type": mapping.sheet_type,
                "column_mapping": json_load(mapping.column_mapping_json, {}),
            },
        }

    def _audit(
        self, session, *, action: str, record: dict, file_count: int,
        business_summary: dict[str, int] | None = None,
    ) -> None:
        warning_count, error_count = NormalizedImportRepository(session).issue_counts(
            record["import_id"]
        )
        AuditService(AuditLogRepository(session)).record(
            store_id=record["store_id"],
            action=action,
            resource_type="import",
            resource_id=record["import_id"],
            after={
                "import_id": record["import_id"],
                "store_id": record["store_id"],
                "status": record["internal_status"],
                "file_count": file_count,
                "profile_count": len(record["sheets"]),
                "warning_count": warning_count,
                "error_count": error_count,
                **(
                    {
                        "business_schema_version": SCHEMA_VERSION,
                        "business_summary": business_summary,
                        "business_rows_created": sum(
                            value for key, value in business_summary.items()
                            if key.endswith("_created")
                        ),
                        "business_rows_updated": sum(
                            value for key, value in business_summary.items()
                            if key.endswith("_updated")
                        ),
                        "duplicates_skipped": business_summary["rows_skipped"],
                    }
                    if business_summary is not None
                    else {}
                ),
            },
            source="import_api",
        )

    def _mark_failed(self, import_id: str, exc: Exception) -> None:
        with self.session_factory() as session:
            repository = NormalizedImportRepository(session)
            job = repository.get_job(import_id)
            if job is None:
                return
            job.status = "failed"
            job.legacy_status = "failed"
            job.failed_at = datetime.now(timezone.utc)
            job.failure_code = getattr(exc, "code", "PROCESSING_ERROR")
            job.failure_message = (
                getattr(exc, "message", None) or "Import processing failed."
            )
            repository.replace_issues(
                import_id=import_id,
                profile_id=None,
                issue_source=(
                    "business_persistence"
                    if isinstance(exc, ValidationError)
                    else "processing"
                ),
                errors=[job.failure_message],
            )
            session.flush()
            record = repository.to_record(job)
            repository.sync_legacy(record)
            self._audit(
                session,
                action="import_failed",
                record=record,
                file_count=len(repository.files(import_id)),
            )
            session.commit()

    @staticmethod
    def _cleanup_files(paths: list[Path], import_dir: Path | None) -> None:
        for path in paths:
            path.unlink(missing_ok=True)
        if import_dir is not None and import_dir.exists():
            try:
                import_dir.rmdir()
            except OSError:
                pass

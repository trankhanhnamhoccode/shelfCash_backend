import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.import_legacy import ImportModel
from app.models.import_normalized import (
    ImportFileModel,
    ImportIssueModel,
    ImportJobModel,
    ImportMappingModel,
    ImportSheetProfileModel,
)


PUBLIC_STATUS = {
    "uploaded": "awaiting_review",
    "mapping_required": "awaiting_review",
    "confirmed": "confirmed",
    "processing": "confirmed",
    "completed": "processed",
    "failed": "failed",
}
INTERNAL_STATUS = {
    "uploaded": "uploaded",
    "awaiting_review": "mapping_required",
    "mapping_required": "mapping_required",
    "confirmed": "confirmed",
    "processing": "processing",
    "processed": "completed",
    "completed": "completed",
    "failed": "failed",
}


def json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def json_load(value: str | None, default):
    return default if value is None else json.loads(value)


class NormalizedImportRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_job(self, import_id: str) -> ImportJobModel | None:
        return self.session.get(ImportJobModel, import_id)

    def get_legacy(self, import_id: str) -> ImportModel | None:
        return self.session.get(ImportModel, import_id)

    def add_job(self, job: ImportJobModel) -> ImportJobModel:
        self.session.add(job)
        return job

    def add_file(self, model: ImportFileModel) -> ImportFileModel:
        self.session.add(model)
        return model

    def add_profile(self, model: ImportSheetProfileModel) -> ImportSheetProfileModel:
        self.session.add(model)
        return model

    def add_mapping(self, model: ImportMappingModel) -> ImportMappingModel:
        self.session.add(model)
        return model

    def files(self, import_id: str) -> list[ImportFileModel]:
        return list(
            self.session.scalars(
                select(ImportFileModel)
                .where(ImportFileModel.import_id == import_id)
                .order_by(ImportFileModel.created_at, ImportFileModel.import_file_id)
            )
        )

    def profiles(self, import_id: str) -> list[ImportSheetProfileModel]:
        return list(
            self.session.scalars(
                select(ImportSheetProfileModel)
                .where(ImportSheetProfileModel.import_id == import_id)
                .order_by(
                    ImportSheetProfileModel.created_at,
                    ImportSheetProfileModel.compatibility_sheet_id,
                )
            )
        )

    def mappings(self, import_id: str) -> list[ImportMappingModel]:
        return list(
            self.session.scalars(
                select(ImportMappingModel).where(ImportMappingModel.import_id == import_id)
            )
        )

    def profile_by_reference(
        self,
        import_id: str,
        *,
        profile_id: str | None = None,
        sheet_id: str | None = None,
    ) -> ImportSheetProfileModel | None:
        statement = select(ImportSheetProfileModel).where(
            ImportSheetProfileModel.import_id == import_id
        )
        if profile_id is not None:
            statement = statement.where(ImportSheetProfileModel.profile_id == profile_id)
        if sheet_id is not None:
            statement = statement.where(
                ImportSheetProfileModel.compatibility_sheet_id == sheet_id
            )
        return self.session.scalar(statement)

    def mapping_for_profile(self, profile_id: str) -> ImportMappingModel | None:
        return self.session.scalar(
            select(ImportMappingModel).where(ImportMappingModel.profile_id == profile_id)
        )

    def replace_issues(
        self,
        *,
        import_id: str,
        profile_id: str | None,
        issue_source: str,
        warnings: list[str] | None = None,
        errors: list[str] | None = None,
        row_errors: list[dict[str, Any]] | None = None,
    ) -> None:
        statement = delete(ImportIssueModel).where(
            ImportIssueModel.import_id == import_id,
            ImportIssueModel.issue_source == issue_source,
        )
        statement = (
            statement.where(ImportIssueModel.profile_id.is_(None))
            if profile_id is None
            else statement.where(ImportIssueModel.profile_id == profile_id)
        )
        self.session.execute(statement)
        for severity, messages in (("warning", warnings or []), ("error", errors or [])):
            for message in messages:
                self.session.add(
                    ImportIssueModel(
                        issue_id=str(uuid4()),
                        import_id=import_id,
                        profile_id=profile_id,
                        severity=severity,
                        code="MAPPING_WARNING" if severity == "warning" else "MAPPING_ERROR",
                        message=message,
                        issue_source=issue_source,
                    )
                )
        for item in row_errors or []:
            self.session.add(
                ImportIssueModel(
                    issue_id=str(uuid4()),
                    import_id=import_id,
                    profile_id=profile_id,
                    source_row=item.get("row_number", item.get("row")),
                    severity="error",
                    code=item.get("code", "ROW_VALIDATION_ERROR"),
                    message=item.get("message", "Canonical row validation failed."),
                    details_json=json_dump(item),
                    issue_source=issue_source,
                )
            )

    def issue_counts(self, import_id: str) -> tuple[int, int]:
        issues = self.session.scalars(
            select(ImportIssueModel).where(ImportIssueModel.import_id == import_id)
        )
        warning_count = error_count = 0
        for issue in issues:
            if issue.severity == "warning":
                warning_count += 1
            else:
                error_count += 1
        return warning_count, error_count

    def to_record(self, job: ImportJobModel) -> dict[str, Any]:
        files_by_id = {item.import_file_id: item for item in self.files(job.import_id)}
        mappings_by_profile = {
            item.profile_id: item for item in self.mappings(job.import_id)
        }
        sheets: list[dict[str, Any]] = []
        warnings: list[str] = []
        errors: list[str] = []
        for profile in self.profiles(job.import_id):
            mapping = mappings_by_profile[profile.profile_id]
            mapping_dict = {
                "sheet_type": mapping.sheet_type,
                "confidence": mapping.confidence,
                "column_mapping": json_load(mapping.column_mapping_json, {}),
                "warnings": json_load(mapping.warnings_json, []),
                "errors": json_load(mapping.errors_json, []),
                "source": mapping.source,
                "requires_review": mapping.requires_review,
            }
            warnings.extend(mapping_dict["warnings"])
            errors.extend(mapping_dict["errors"])
            source_file = files_by_id[profile.import_file_id]
            sheets.append(
                {
                    "sheet_id": profile.compatibility_sheet_id,
                    "profile_id": profile.profile_id,
                    "stored_name": source_file.stored_file_name,
                    "profile": {
                        "file_name": source_file.original_file_name,
                        "sheet_name": profile.sheet_name,
                        "header_row_zero_based": profile.header_row_zero_based,
                        "row_count": profile.row_count,
                        "column_count": profile.column_count,
                        "columns": json_load(profile.columns_json, []),
                        "dtypes": json_load(profile.dtypes_json, {}),
                        "sample_rows": json_load(profile.sample_rows_json, []),
                    },
                    "mapping": mapping_dict,
                    "rows": json_load(profile.parsed_rows_json, []),
                }
            )
        return {
            "import_id": job.import_id,
            "status": PUBLIC_STATUS.get(job.status, job.legacy_status or job.status),
            "internal_status": job.status,
            "store_id": job.store_id,
            "forecast_date": job.forecast_date.isoformat() if job.forecast_date else None,
            "forecast_horizon": job.forecast_horizon,
            "sheets": sheets,
            "warnings": warnings,
            "errors": errors,
            "requires_review": job.requires_review,
            "created_at": job.created_at,
            "result": json_load(job.result_json, None),
            "validation_summary": json_load(job.validation_summary_json, {}),
        }

    def sync_legacy(self, record: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc)
        payload = {
            key: value
            for key, value in record.items()
            if key not in {"import_id", "internal_status", "validation_summary"}
        }
        payload["created_at"] = (
            record["created_at"].isoformat()
            if hasattr(record.get("created_at"), "isoformat")
            else record.get("created_at")
        )
        legacy = self.get_legacy(record["import_id"])
        if legacy is None:
            legacy = ImportModel(
                id=record["import_id"],
                status=record["status"],
                payload=json_dump(payload),
                created_at=record["created_at"],
                updated_at=now,
            )
            self.session.add(legacy)
        else:
            legacy.status = record["status"]
            legacy.payload = json_dump(payload)
            legacy.updated_at = now

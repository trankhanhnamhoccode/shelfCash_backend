from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.core.canonical_schemas import CANONICAL_SCHEMAS
from app.core.excel_reader import ExcelIngestionError, read_workbook, sanitize_filename
from app.schemas.canonical import CanonicalResult


class ImportService:
    def __init__(self, repository, pipeline, settings):
        self.repository, self.pipeline, self.settings = repository, pipeline, settings

    async def create_import(self, uploads, store_id: str, forecast_date: date | None, forecast_horizon: int):
        if len(uploads) > self.settings.max_files_per_request:
            raise ExcelIngestionError("too_many_files", f"Maximum {self.settings.max_files_per_request} files per request")
        import_id = str(uuid4())
        sheets, warnings, errors = [], [], []
        import_dir = self.settings.upload_dir / import_id
        import_dir.mkdir(parents=True, exist_ok=True)
        for upload in uploads:
            safe_name = sanitize_filename(upload.filename)
            content = await upload.read(self.settings.max_file_size_mb * 1024 * 1024 + 1)
            if len(content) > self.settings.max_file_size_mb * 1024 * 1024:
                raise ExcelIngestionError("file_too_large", f"File '{safe_name}' exceeds {self.settings.max_file_size_mb} MB")
            parsed = read_workbook(content, safe_name, self.settings.max_sheets_per_file, self.settings.max_rows_per_sheet, self.settings.sample_rows_per_sheet)
            stored_name = f"{uuid4().hex}_{safe_name}"
            (import_dir / stored_name).write_bytes(content)
            for item in parsed:
                suggestion = await self.pipeline.suggest(item.profile)
                sheets.append({
                    "sheet_id": f"{stored_name}:{item.sheet_id}",
                    "stored_name": stored_name,
                    "profile": item.profile.model_dump(mode="json"),
                    "mapping": suggestion.model_dump(mode="json"),
                    "rows": item.rows,
                })
                warnings.extend(suggestion.warnings)
                errors.extend(suggestion.errors)
        record = {
            "import_id": import_id, "status": "awaiting_review", "store_id": store_id,
            "forecast_date": forecast_date.isoformat() if forecast_date else None,
            "forecast_horizon": forecast_horizon, "sheets": sheets, "warnings": warnings, "errors": errors,
            "requires_review": any(s["mapping"]["requires_review"] for s in sheets),
            "created_at": datetime.now(timezone.utc).isoformat(), "result": None,
        }
        self.repository.create(record)
        return self.repository.get(import_id)

    def get(self, import_id):
        return self.repository.get(str(import_id))

    def confirm(self, import_id, confirmed):
        record = self._require(import_id)
        by_id = {item.sheet_id: item for item in confirmed}
        for sheet in record["sheets"]:
            if sheet["sheet_id"] not in by_id:
                continue
            request = by_id[sheet["sheet_id"]]
            from app.schemas.llm import SheetProfile
            validated = self.pipeline.confirm(SheetProfile.model_validate(sheet["profile"]), request.sheet_type, request.column_mapping)
            sheet["mapping"] = validated.model_dump(mode="json")
        requires_review = any(s["mapping"]["requires_review"] for s in record["sheets"])
        return self.repository.update(str(import_id), sheets=record["sheets"], status="confirmed", requires_review=requires_review)

    def process(self, import_id):
        record = self._require(import_id)
        result = {kind: [] for kind in CANONICAL_SCHEMAS if kind != "unknown"}
        summary = {}
        for sheet in record["sheets"]:
            kind, rows, validation = self.pipeline.process_sheet(sheet)
            if kind != "unknown":
                result[kind].extend(rows)
            summary[sheet["sheet_id"]] = validation
        canonical = CanonicalResult(
            store_id=record["store_id"], forecast_date=record["forecast_date"],
            forecast_horizon=record["forecast_horizon"], **result, validation_summary=summary,
            ingestion_metadata={"import_id": str(import_id), "processed_at": datetime.now(timezone.utc).isoformat(), "sheet_count": len(record["sheets"])},
        ).model_dump(mode="json")
        result_path = self.settings.result_dir / f"{import_id}.json"
        result_path.write_text(__import__("json").dumps(canonical, ensure_ascii=False), encoding="utf-8")
        updated = self.repository.update(str(import_id), status="processed", result=canonical)
        return updated

    def result(self, import_id):
        record = self._require(import_id)
        return record.get("result")

    def _require(self, import_id):
        record = self.get(import_id)
        if not record:
            raise KeyError(str(import_id))
        return record

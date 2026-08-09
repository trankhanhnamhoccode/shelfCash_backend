from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Header, UploadFile, status

from app.dependencies import get_service, require_api_key
from app.schemas.imports import ConfirmRequest, ImportResponse, ProcessResponse, StatusResponse

router = APIRouter(prefix="/imports", tags=["imports"], dependencies=[Depends(require_api_key)])


def _public_import(record):
    sheets = [
        {
            "sheet_id": sheet["sheet_id"],
            "profile_id": sheet["profile_id"],
            "profile": sheet["profile"],
            "mapping": sheet["mapping"],
        }
        for sheet in record["sheets"]
    ]
    profiles = [
        {
            "profile_id": sheet["profile_id"],
            "sheet_id": sheet["sheet_id"],
            **sheet["profile"],
        }
        for sheet in record["sheets"]
    ]
    suggested_mappings = [
        {
            "profile_id": sheet["profile_id"],
            "sheet_id": sheet["sheet_id"],
            "sheet_name": sheet["profile"]["sheet_name"],
            **sheet["mapping"],
        }
        for sheet in record["sheets"]
    ]
    return {
        "import_id": record["import_id"], "status": record["status"], "store_id": record.get("store_id"),
        "forecast_date": record.get("forecast_date"), "forecast_horizon": record.get("forecast_horizon"),
        "sheets": sheets, "profiles": profiles, "suggested_mappings": suggested_mappings,
        "warnings": record.get("warnings", []), "errors": record.get("errors", []),
        "requires_review": record.get("requires_review", False), "created_at": record.get("created_at"),
    }


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ImportResponse)
async def create_import(
    files: list[UploadFile] = File(...), store_id: str = Form(...),
    forecast_date: date | None = Form(default=None), forecast_horizon: int = Form(default=7, ge=1, le=90),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    service=Depends(get_service),
):
    return _public_import(
        await service.create_import(
            files, store_id, forecast_date, forecast_horizon, idempotency_key
        )
    )


@router.get("/{import_id}", response_model=StatusResponse)
def get_import(import_id: UUID, service=Depends(get_service)):
    record = service.get(import_id)
    public = _public_import(record)
    return {
        "import_id": record["import_id"],
        "status": record["status"],
        "mappings": public["sheets"],
        "sheets": public["sheets"],
        "profiles": public["profiles"],
        "suggested_mappings": public["suggested_mappings"],
        "requires_review": record["requires_review"],
    }


@router.post("/{import_id}/confirm", response_model=StatusResponse)
def confirm_import(import_id: UUID, payload: ConfirmRequest, service=Depends(get_service)):
    record = service.confirm(import_id, payload.mappings)
    public = _public_import(record)
    return {
        "import_id": record["import_id"],
        "status": record["status"],
        "mappings": public["sheets"],
        "sheets": public["sheets"],
        "profiles": public["profiles"],
        "suggested_mappings": public["suggested_mappings"],
        "requires_review": record["requires_review"],
    }


@router.post("/{import_id}/process", response_model=ProcessResponse)
def process_import(
    import_id: UUID,
    policy: Literal["atomic", "partial_success", "preview_only"] = "atomic",
    service=Depends(get_service),
):
    record = service.process(import_id, policy=policy)
    result = record["result"]
    return {
        "import_id": record["import_id"], "status": record["status"],
        "validation_summary": result["validation_summary"],
        "processing_policy": result.get("ingestion_metadata", {}).get("processing_policy", policy),
        "issues": result.get("issues", []),
    }


@router.get("/{import_id}/result")
def get_result(import_id: UUID, service=Depends(get_service)):
    return service.result(import_id)

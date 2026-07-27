from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from app.dependencies import get_service, require_api_key
from app.schemas.imports import ConfirmRequest, ImportResponse, ProcessResponse, StatusResponse

router = APIRouter(prefix="/imports", tags=["imports"], dependencies=[Depends(require_api_key)])


def _public_import(record):
    sheets = [{"sheet_id": s["sheet_id"], "profile": s["profile"], "mapping": s["mapping"]} for s in record["sheets"]]
    return {
        "import_id": record["import_id"], "status": record["status"], "store_id": record.get("store_id"),
        "forecast_date": record.get("forecast_date"), "forecast_horizon": record.get("forecast_horizon"),
        "sheets": sheets, "warnings": record.get("warnings", []), "errors": record.get("errors", []),
        "requires_review": record.get("requires_review", False), "created_at": record.get("created_at"),
    }


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ImportResponse)
async def create_import(
    files: list[UploadFile] = File(...), store_id: str = Form(...),
    forecast_date: date | None = Form(default=None), forecast_horizon: int = Form(default=7, ge=1),
    service=Depends(get_service),
):
    return _public_import(await service.create_import(files, store_id, forecast_date, forecast_horizon))


@router.get("/{import_id}", response_model=StatusResponse)
def get_import(import_id: UUID, service=Depends(get_service)):
    record = service.get(import_id)
    if not record:
        raise HTTPException(404, detail={"code": "import_not_found", "message": "Import not found", "details": {}})
    return {"import_id": record["import_id"], "status": record["status"], "mappings": _public_import(record)["sheets"], "requires_review": record["requires_review"]}


@router.post("/{import_id}/confirm", response_model=StatusResponse)
def confirm_import(import_id: UUID, payload: ConfirmRequest, service=Depends(get_service)):
    try:
        record = service.confirm(import_id, payload.mappings)
    except KeyError:
        raise HTTPException(404, detail={"code": "import_not_found", "message": "Import not found", "details": {}})
    except ValueError as exc:
        raise HTTPException(422, detail={"code": "invalid_mapping", "message": str(exc), "details": {}})
    return {"import_id": record["import_id"], "status": record["status"], "mappings": _public_import(record)["sheets"], "requires_review": record["requires_review"]}


@router.post("/{import_id}/process", response_model=ProcessResponse)
def process_import(import_id: UUID, service=Depends(get_service)):
    try:
        record = service.process(import_id)
    except KeyError:
        raise HTTPException(404, detail={"code": "import_not_found", "message": "Import not found", "details": {}})
    return {"import_id": record["import_id"], "status": record["status"], "validation_summary": record["result"]["validation_summary"]}


@router.get("/{import_id}/result")
def get_result(import_id: UUID, service=Depends(get_service)):
    try:
        result = service.result(import_id)
    except KeyError:
        raise HTTPException(404, detail={"code": "import_not_found", "message": "Import not found", "details": {}})
    if result is None:
        raise HTTPException(409, detail={"code": "result_not_ready", "message": "Import has not been processed", "details": {}})
    return result

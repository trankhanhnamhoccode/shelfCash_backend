from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from app.api import health, imports, llm
from app.config import Settings, get_settings
from app.core.excel_reader import ExcelIngestionError
from app.core.ingestion_pipeline import IngestionPipeline
from app.llm.factory import create_llm_provider
from app.repositories.sqlite_imports import SQLiteImportRepository
from app.services.import_service import ImportService


def create_app(settings: Settings | None = None) -> FastAPI:
    active_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        active_settings.upload_dir.mkdir(parents=True, exist_ok=True)
        active_settings.result_dir.mkdir(parents=True, exist_ok=True)
        repository = SQLiteImportRepository(active_settings.database_url)
        provider = create_llm_provider(active_settings)
        if active_settings.llm_provider == "local_qwen":
            await provider.load()
        pipeline = IngestionPipeline(provider, active_settings.rule_confidence_threshold)
        app.state.settings = active_settings
        app.state.llm_provider = provider
        app.state.import_service = ImportService(repository, pipeline, active_settings)
        yield
        await provider.close()

    app = FastAPI(title=active_settings.app_name, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware, allow_origins=active_settings.cors_origins, allow_credentials=True,
        allow_methods=["*"], allow_headers=["*"],
    )
    app.include_router(health.router)
    app.include_router(llm.router, prefix="/api/v1")
    app.include_router(imports.router, prefix="/api/v1")

    @app.exception_handler(ExcelIngestionError)
    async def excel_error(_: Request, exc: ExcelIngestionError):
        return JSONResponse(status_code=400, content={"code": exc.code, "message": exc.message, "details": exc.details})

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException):
        if isinstance(exc.detail, dict) and {"code", "message", "details"} <= set(exc.detail):
            content = exc.detail
        else:
            content = {"code": "http_error", "message": str(exc.detail), "details": {}}
        return JSONResponse(status_code=exc.status_code, content=content, headers=exc.headers)

    @app.exception_handler(ValidationError)
    async def validation_error(_: Request, exc: ValidationError):
        return JSONResponse(status_code=422, content={"code": "validation_error", "message": "Request validation failed", "details": {"errors": exc.errors()}})

    @app.exception_handler(RequestValidationError)
    async def request_validation_error(_: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={"code": "validation_error", "message": "Request validation failed", "details": {"errors": exc.errors()}},
        )
    return app


app = create_app()

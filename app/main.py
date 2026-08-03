from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError as PydanticValidationError

from app.api import catalog, completion, forecast, health, imports, llm, menu, operational, recipes
from app.config import Settings, get_settings
from app.core.exceptions import ShelfCashError
from app.core.excel_reader import ExcelIngestionError
from app.core.ingestion_pipeline import IngestionPipeline
from app.core.request_id import RequestIdMiddleware
from app.db.session import create_engine_from_settings, create_session_factory
from app.llm.factory import create_llm_provider
from app.services.import_service import ImportService
from app.services.catalog_service import CatalogApiService, RecipeApiService
from app.services.operational_service import OperationalService
from app.services.completion_service import CompletionService
from app.services.menu_service import MenuService
from app.services.forecast_service import ForecastService


logger = logging.getLogger("shelfcash.api")


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _error_content(request: Request, code: str, message: str, details: dict) -> dict:
    return {
        "code": code,
        "message": message,
        "details": details,
        "request_id": _request_id(request),
    }


def _error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: dict,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    response_headers = dict(headers or {})
    request_id = _request_id(request)
    if request_id:
        response_headers["X-Request-ID"] = request_id
    return JSONResponse(
        status_code=status_code,
        content=_error_content(request, code, message, details),
        headers=response_headers,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    active_settings = settings or get_settings()
    logging.basicConfig(
        level=getattr(logging, active_settings.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("shelfcash").setLevel(active_settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        active_settings.upload_dir.mkdir(parents=True, exist_ok=True)
        active_settings.result_dir.mkdir(parents=True, exist_ok=True)
        active_settings.forecast_artifact_root.mkdir(parents=True, exist_ok=True)
        engine = create_engine_from_settings(active_settings)
        session_factory = create_session_factory(engine)
        provider = create_llm_provider(active_settings)
        app.state.settings = active_settings
        app.state.engine = engine
        app.state.session_factory = session_factory
        app.state.llm_provider = provider
        try:
            if active_settings.llm_provider == "local_qwen":
                await provider.load()
            pipeline = IngestionPipeline(provider, active_settings.rule_confidence_threshold)
            app.state.import_service = ImportService(
                session_factory, pipeline, active_settings
            )
            app.state.catalog_service = CatalogApiService(session_factory)
            app.state.menu_service = MenuService(session_factory)
            app.state.recipe_api_service = RecipeApiService(session_factory)
            app.state.operational_service = OperationalService(session_factory)
            app.state.forecast_service = ForecastService(session_factory, active_settings)
            app.state.completion_service = CompletionService(session_factory, app.state.operational_service, app.state.forecast_service)
            yield
        finally:
            await provider.close()
            engine.dispose()

    app = FastAPI(title=active_settings.app_name, lifespan=lifespan)
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(
        CORSMiddleware, allow_origins=active_settings.cors_origins, allow_credentials=True,
        allow_methods=["*"], allow_headers=["*"],
    )
    app.include_router(health.router)
    app.include_router(llm.router, prefix="/api/v1")
    app.include_router(imports.router, prefix="/api/v1")
    app.include_router(catalog.router, prefix="/api/v1")
    app.include_router(menu.router, prefix="/api/v1")
    app.include_router(recipes.router, prefix="/api/v1")
    app.include_router(operational.router, prefix="/api/v1")
    app.include_router(completion.router, prefix="/api/v1")
    app.include_router(forecast.router, prefix="/api/v1")

    @app.exception_handler(ExcelIngestionError)
    async def excel_error(request: Request, exc: ExcelIngestionError):
        logger.warning(
            "request_failed request_id=%s stage=excel_ingestion code=%s status=%s details=%r",
            _request_id(request), exc.code, exc.status_code, exc.details,
        )
        return _error_response(
            request,
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            details=exc.details,
        )

    @app.exception_handler(ShelfCashError)
    async def domain_error(request: Request, exc: ShelfCashError):
        logger.warning(
            "request_failed request_id=%s stage=domain_validation code=%s status=%s details=%r",
            _request_id(request), exc.code, exc.http_status, exc.details,
        )
        return _error_response(
            request,
            status_code=exc.http_status,
            code=exc.code,
            message=exc.message,
            details=exc.details,
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        logger.warning(
            "request_failed request_id=%s stage=http_validation status=%s detail=%r",
            _request_id(request), exc.status_code, exc.detail,
        )
        if isinstance(exc.detail, dict) and {"code", "message", "details"} <= set(exc.detail):
            content = _error_content(
                request,
                str(exc.detail["code"]),
                str(exc.detail["message"]),
                exc.detail["details"],
            )
        else:
            content = _error_content(request, "http_error", str(exc.detail), {})
        response_headers = dict(exc.headers or {})
        request_id = _request_id(request)
        if request_id:
            response_headers["X-Request-ID"] = request_id
        return JSONResponse(status_code=exc.status_code, content=content, headers=response_headers)

    @app.exception_handler(PydanticValidationError)
    async def validation_error(request: Request, exc: PydanticValidationError):
        logger.warning(
            "request_failed request_id=%s stage=response_validation status=422 errors=%r",
            _request_id(request), exc.errors(),
        )
        return _error_response(
            request,
            status_code=422,
            code="validation_error",
            message="Request validation failed",
            details={"errors": exc.errors()},
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_error(request: Request, exc: RequestValidationError):
        logger.warning(
            "request_failed request_id=%s stage=request_validation status=422 errors=%r",
            _request_id(request), exc.errors(),
        )
        return _error_response(
            request,
            status_code=422,
            code="validation_error",
            message="Request validation failed",
            details={"errors": exc.errors()},
        )

    @app.exception_handler(Exception)
    async def unknown_error(request: Request, exc: Exception):
        logger.exception(
            "request_failed request_id=%s stage=unhandled status=500 exception_type=%s",
            _request_id(request), type(exc).__name__,
            exc_info=exc,
        )
        return _error_response(
            request,
            status_code=500,
            code="INTERNAL_ERROR",
            message="Internal server error.",
            details={},
        )
    return app


app = create_app()

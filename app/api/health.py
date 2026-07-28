from fastapi import APIRouter, Request

from app.db.health import check_database

router = APIRouter()


@router.get("/health")
def health(request: Request):
    check_database(request.app.state.session_factory)
    settings = request.app.state.settings
    return {
        "status": "ok",
        "service": "shelfcash-backend",
        "version": settings.app_version,
        "database": "ready",
    }

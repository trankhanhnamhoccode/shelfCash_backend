import secrets

from fastapi import Header, HTTPException, Request

from app.db.unit_of_work import UnitOfWork


def get_service(request: Request):
    return request.app.state.import_service


def get_llm_provider(request: Request):
    return request.app.state.llm_provider


def get_catalog_service(request: Request):
    return request.app.state.catalog_service


def get_menu_service(request: Request):
    return request.app.state.menu_service


def get_recipe_api_service(request: Request):
    return request.app.state.recipe_api_service

def get_operational_service(request: Request):
    return request.app.state.operational_service
def get_completion_service(request: Request):
    return request.app.state.completion_service


def get_db_session(request: Request):
    session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


def get_unit_of_work(request: Request) -> UnitOfWork:
    return UnitOfWork(request.app.state.session_factory)


def require_api_key(request: Request, x_shelfcash_key: str | None = Header(default=None)):
    expected = request.app.state.settings.shelfcash_api_key
    if expected and (not x_shelfcash_key or not secrets.compare_digest(expected, x_shelfcash_key)):
        raise HTTPException(status_code=401, detail={"code": "unauthorized", "message": "Invalid or missing API key", "details": {}})

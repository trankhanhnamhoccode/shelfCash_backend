import secrets

from fastapi import Header, HTTPException, Request


def get_service(request: Request):
    return request.app.state.import_service


def get_llm_provider(request: Request):
    return request.app.state.llm_provider


def require_api_key(request: Request, x_shelfcash_key: str | None = Header(default=None)):
    expected = request.app.state.settings.shelfcash_api_key
    if expected and (not x_shelfcash_key or not secrets.compare_digest(expected, x_shelfcash_key)):
        raise HTTPException(status_code=401, detail={"code": "unauthorized", "message": "Invalid or missing API key", "details": {}})

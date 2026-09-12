from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from app.schemas.errors import ErrorResponse


_HTTP_METHODS = {"delete", "get", "head", "options", "patch", "post", "put"}
_ERROR_SCHEMA_REF = "#/components/schemas/ErrorResponse"

# These are the concrete non-validation error outcomes frozen by R1.1. They
# deliberately cover only operations with direct runtime evidence, rather than
# advertising every possible status on every endpoint.
_OPERATION_ERROR_RESPONSES = {
    ("/api/v1/imports/{import_id}", "get"): {
        "404": "Import not found.",
    },
    ("/api/v1/stores/{store_id}/ingredients", "post"): {
        "409": "Ingredient conflict.",
    },
    ("/api/v1/forecast-models/train", "post"): {
        "500": "Forecast training failed.",
    },
}


def _error_response(description: str) -> dict:
    return {
        "description": description,
        "content": {
            "application/json": {
                "schema": {"$ref": _ERROR_SCHEMA_REF},
            }
        },
    }


def _requires_api_key(operation: dict) -> bool:
    return any(
        parameter.get("in") == "header"
        and parameter.get("name") == "x-shelfcash-key"
        for parameter in operation.get("parameters", [])
    )


def build_openapi(app: FastAPI) -> dict:
    if app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    components = schema.setdefault("components", {}).setdefault("schemas", {})
    components["ErrorResponse"] = ErrorResponse.model_json_schema(
        ref_template="#/components/schemas/{model}"
    )

    for path, path_item in schema["paths"].items():
        for method, operation in path_item.items():
            if method not in _HTTP_METHODS:
                continue
            responses = operation.get("responses", {})
            if "422" in responses:
                responses["422"] = _error_response("Request validation failed.")
            if _requires_api_key(operation):
                responses.setdefault("401", _error_response("Invalid or missing API key."))
            for status, description in _OPERATION_ERROR_RESPONSES.get((path, method), {}).items():
                responses.setdefault(status, _error_response(description))

    # FastAPI generated these only for its replaced automatic validation
    # response. Keep the published component set free of that stale contract.
    components.pop("HTTPValidationError", None)
    components.pop("ValidationError", None)
    app.openapi_schema = schema
    return schema

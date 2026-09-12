from app.core.exceptions import ForecastError


ERROR_SCHEMA_REF = "#/components/schemas/ErrorResponse"


def _response_schema(document, path, method, status):
    return document["paths"][path][method]["responses"][str(status)]["content"][
        "application/json"
    ]["schema"]


def _assert_error_envelope(response):
    assert set(response.json()) == {"code", "message", "details", "request_id"}
    assert response.json()["request_id"] == response.headers["X-Request-ID"]


def test_validation_envelope_and_openapi_contract_are_aligned(client):
    response = client.get("/api/v1/imports/not-a-uuid")

    assert response.status_code == 422
    _assert_error_envelope(response)
    assert response.json()["code"] == "validation_error"
    assert response.json()["details"]["errors"]

    document = client.get("/openapi.json").json()
    assert _response_schema(document, "/api/v1/imports/{import_id}", "get", 422) == {
        "$ref": ERROR_SCHEMA_REF
    }
    assert "HTTPValidationError" not in document["components"]["schemas"]
    assert {"code", "message", "details", "request_id"} <= set(
        document["components"]["schemas"]["ErrorResponse"]["required"]
    )


def test_auth_not_found_and_conflict_use_the_common_contract(client):
    client.app.state.settings.shelfcash_api_key = "secret"
    auth = client.post("/api/v1/forecasts", json={})
    assert auth.status_code == 401
    _assert_error_envelope(auth)
    assert auth.json()["code"] == "unauthorized"

    headers = {"X-ShelfCash-Key": "secret"}
    missing = client.get(
        "/api/v1/imports/00000000-0000-0000-0000-000000000000", headers=headers
    )
    assert missing.status_code == 404
    _assert_error_envelope(missing)
    assert missing.json()["code"] == "IMPORT_NOT_FOUND"

    endpoint = "/api/v1/stores/STORE_001/ingredients"
    body = {"ingredient": "R1.1 conflict", "sku": "R11-CONFLICT", "base_unit": "kg"}
    assert client.post(endpoint, headers=headers, json=body).status_code == 201
    conflict = client.post(endpoint, headers=headers, json=body)
    assert conflict.status_code == 409
    _assert_error_envelope(conflict)

    document = client.get("/openapi.json").json()
    assert _response_schema(document, "/api/v1/forecasts", "post", 401) == {
        "$ref": ERROR_SCHEMA_REF
    }
    assert _response_schema(document, "/api/v1/imports/{import_id}", "get", 404) == {
        "$ref": ERROR_SCHEMA_REF
    }
    assert _response_schema(document, "/api/v1/stores/{store_id}/ingredients", "post", 409) == {
        "$ref": ERROR_SCHEMA_REF
    }


def test_server_error_uses_the_common_contract_and_openapi_schema(client, monkeypatch):
    def fail_training(*_args, **_kwargs):
        raise ForecastError(
            "FORECAST_TRAINING_FAILED", "training failed", http_status=500
        )

    monkeypatch.setattr(client.app.state.forecast_service, "train", fail_training)
    response = client.post(
        "/api/v1/forecast-models/train",
        json={"store_id": "STORE_001", "cutoff_date": "2026-08-03"},
    )

    assert response.status_code == 500
    _assert_error_envelope(response)
    assert response.json()["code"] == "FORECAST_TRAINING_FAILED"

    document = client.get("/openapi.json").json()
    assert _response_schema(document, "/api/v1/forecast-models/train", "post", 500) == {
        "$ref": ERROR_SCHEMA_REF
    }


def test_openapi_manifest_and_representative_success_schemas_remain_frozen(client):
    document = client.get("/openapi.json").json()
    operations = {
        (method, path)
        for path, path_item in document["paths"].items()
        for method in path_item
        if method in {"delete", "get", "head", "options", "patch", "post", "put"}
    }

    assert len(document["paths"]) == 58
    assert len(operations) == 70
    assert _response_schema(document, "/api/v1/imports/{import_id}", "get", 200) == {
        "$ref": "#/components/schemas/StatusResponse"
    }
    assert _response_schema(document, "/api/v1/stores/{store_id}/ingredients", "post", 201) == {
        "$ref": "#/components/schemas/IngredientResponse"
    }
    assert _response_schema(document, "/api/v1/forecasts", "post", 201) == {
        "$ref": "#/components/schemas/ForecastResponse"
    }

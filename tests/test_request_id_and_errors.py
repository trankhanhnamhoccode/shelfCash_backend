from fastapi.testclient import TestClient

from app.config import Settings
from app.core.exceptions import StoreNotFoundError
from app.main import create_app
from tests.conftest import migrate_database


def test_incoming_request_id_is_preserved(client):
    response = client.get("/health", headers={"X-Request-ID": "request-123"})
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "request-123"


def test_validation_error_has_contract_envelope(client):
    response = client.get("/api/v1/imports/not-a-uuid")
    assert response.status_code == 422
    assert set(response.json()) == {"code", "message", "details", "request_id"}
    assert response.json()["code"] == "validation_error"
    assert response.json()["request_id"] == response.headers["X-Request-ID"]
    assert response.json()["details"]["errors"]


def test_import_not_found_uses_domain_code_and_request_id(client):
    response = client.get("/api/v1/imports/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
    assert response.json()["code"] == "IMPORT_NOT_FOUND"
    assert response.json()["request_id"] == response.headers["X-Request-ID"]


def test_domain_and_unknown_errors_are_safe(tmp_path):
    database_url = f"sqlite:///{(tmp_path / 'errors.db').as_posix()}"
    migrate_database(database_url)
    settings = Settings(
        database_url=database_url,
        upload_dir=tmp_path / "uploads",
        result_dir=tmp_path / "results",
    )
    app = create_app(settings)

    @app.get("/_test/domain")
    def domain_error():
        raise StoreNotFoundError("STORE_MISSING")

    @app.get("/_test/unknown")
    def unknown_error():
        raise RuntimeError("sensitive internal stack detail")

    with TestClient(app, raise_server_exceptions=False) as client:
        domain = client.get("/_test/domain")
        assert domain.status_code == 404
        assert domain.json()["code"] == "STORE_NOT_FOUND"
        assert domain.json()["details"] == {"store_id": "STORE_MISSING"}

        unknown = client.get("/_test/unknown")
        assert unknown.status_code == 500
        assert unknown.json()["code"] == "INTERNAL_ERROR"
        assert "sensitive" not in unknown.text
        assert unknown.json()["request_id"] == unknown.headers["X-Request-ID"]


def test_health_database_failure_does_not_report_ready(client):
    class BrokenSessionFactory:
        def __call__(self):
            raise OSError("database unavailable")

    client.app.state.session_factory = BrokenSessionFactory()
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json()["code"] == "DATABASE_NOT_READY"
    assert "ready" not in response.text
    assert response.json()["request_id"] == response.headers["X-Request-ID"]

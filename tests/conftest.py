from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.config import Settings
from app.db.session import create_engine_from_url, create_session_factory
from app.main import create_app
from scripts.seed_database import seed_database


def migrate_database(database_url: str) -> None:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, "head")


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    url = f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    migrate_database(url)
    return url


@pytest.fixture
def session_factory(database_url):
    engine = create_engine_from_url(database_url)
    factory = create_session_factory(engine)
    try:
        yield factory
    finally:
        engine.dispose()


@pytest.fixture
def client(tmp_path: Path, database_url: str):
    settings = Settings(
        upload_dir=tmp_path / "uploads", result_dir=tmp_path / "results",
        forecast_artifact_root=tmp_path / "forecast_artifacts",
        database_url=database_url, max_file_size_mb=1,
    )
    engine = create_engine_from_url(database_url)
    seed_database(create_session_factory(engine))
    engine.dispose()
    with TestClient(create_app(settings)) as test_client:
        yield test_client

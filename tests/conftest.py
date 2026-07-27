from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def client(tmp_path: Path):
    settings = Settings(
        llm_provider="disabled", upload_dir=tmp_path / "uploads", result_dir=tmp_path / "results",
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}", max_file_size_mb=1,
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client

import json
from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import (
    Column,
    DateTime,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    inspect,
    select,
)

from app.db.base import Base
from app.db.session import create_engine_from_url, create_session_factory
from app.models.import_legacy import ImportModel
from app.repositories.sqlite_imports import SQLiteImportRepository


FOUNDATIONAL_TABLES = {"imports", "stores", "idempotency_records", "audit_logs"}
NORMALIZED_IMPORT_TABLES = {
    "import_jobs",
    "import_files",
    "import_sheet_profiles",
    "import_mappings",
    "import_issues",
}


def upgrade(database_url: str) -> None:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")


def migrate_to(database_url: str, revision: str) -> None:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, revision)


def test_empty_database_upgrade_to_head(tmp_path: Path):
    database_url = f"sqlite:///{(tmp_path / 'empty.db').as_posix()}"
    upgrade(database_url)
    engine = create_engine_from_url(database_url)
    try:
        assert FOUNDATIONAL_TABLES | NORMALIZED_IMPORT_TABLES | {"alembic_version"} <= set(
            inspect(engine).get_table_names()
        )
    finally:
        engine.dispose()


def test_legacy_database_upgrade_preserves_import_row(tmp_path: Path):
    database_url = f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}"
    engine = create_engine(database_url)
    metadata = MetaData()
    imports = Table(
        "imports",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("status", String(32), nullable=False),
        Column("payload", Text, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
    )
    metadata.create_all(engine)
    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        connection.execute(
            imports.insert().values(
                id="legacy-import",
                status="awaiting_review",
                payload=json.dumps({"store_id": "STORE_OLD", "sheets": []}),
                created_at=now,
                updated_at=now,
            )
        )
    engine.dispose()

    upgrade(database_url)
    shared_engine = create_engine_from_url(database_url)
    try:
        repository = SQLiteImportRepository(create_session_factory(shared_engine))
        record = repository.get("legacy-import")
        assert record is not None
        assert record["status"] == "awaiting_review"
        assert record["store_id"] == "STORE_OLD"
        assert FOUNDATIONAL_TABLES | {"alembic_version"} <= set(
            inspect(shared_engine).get_table_names()
        )
    finally:
        shared_engine.dispose()


def test_shared_metadata_contains_foundational_tables():
    assert FOUNDATIONAL_TABLES <= set(Base.metadata.tables)
    assert ImportModel.metadata is Base.metadata


def test_legacy_repository_create_read_update(session_factory):
    repository = SQLiteImportRepository(session_factory)
    repository.create(
        {
            "import_id": "compat-import",
            "status": "awaiting_review",
            "store_id": "STORE_COMPAT",
            "sheets": [],
        }
    )
    assert repository.get("compat-import")["sheets"] == []
    updated = repository.update("compat-import", status="confirmed")
    assert updated["status"] == "confirmed"


def test_legacy_repository_does_not_use_create_all():
    source = Path("app/repositories/sqlite_imports.py").read_text(encoding="utf-8")
    assert "create_all" not in source
    assert "create_engine(" not in source


def test_upgrade_0001_to_0002_and_downgrade_preserves_legacy_import(tmp_path: Path):
    database_url = f"sqlite:///{(tmp_path / 'upgrade.db').as_posix()}"
    migrate_to(database_url, "20260728_0001")
    engine = create_engine_from_url(database_url)
    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        connection.execute(
            ImportModel.__table__.insert().values(
                id="00000000-0000-0000-0000-000000000002",
                status="awaiting_review",
                payload=json.dumps({"store_id": "STORE_001", "sheets": []}),
                created_at=now,
                updated_at=now,
            )
        )
    engine.dispose()

    migrate_to(database_url, "20260728_0002")
    engine = create_engine_from_url(database_url)
    assert NORMALIZED_IMPORT_TABLES <= set(inspect(engine).get_table_names())
    engine.dispose()

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.downgrade(config, "20260728_0001")
    engine = create_engine_from_url(database_url)
    try:
        tables = set(inspect(engine).get_table_names())
        assert "imports" in tables
        assert not (NORMALIZED_IMPORT_TABLES & tables)
        with engine.connect() as connection:
            assert (
                connection.execute(
                    select(ImportModel.id).where(
                        ImportModel.id == "00000000-0000-0000-0000-000000000002"
                    )
                ).scalar_one()
                == "00000000-0000-0000-0000-000000000002"
            )
    finally:
        engine.dispose()

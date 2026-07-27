import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import DateTime, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.repositories.imports import ImportRepository


class Base(DeclarativeBase):
    pass


class ImportModel(Base):
    __tablename__ = "imports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(32))
    payload: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SQLiteImportRepository(ImportRepository):
    def __init__(self, database_url: str):
        if database_url.startswith("sqlite:///"):
            db_path = Path(database_url.removeprefix("sqlite:///"))
            db_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(database_url, connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)

    def create(self, record: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc)
        data = dict(record)
        import_id = data.pop("import_id")
        status = data.get("status", "uploaded")
        with Session(self.engine) as session:
            session.add(ImportModel(id=import_id, status=status, payload=json.dumps(data, ensure_ascii=False, default=str), created_at=now, updated_at=now))
            session.commit()

    def get(self, import_id: str) -> dict[str, Any] | None:
        with Session(self.engine) as session:
            model = session.get(ImportModel, import_id)
            if not model:
                return None
            data = json.loads(model.payload)
            return {"import_id": model.id, **data, "status": model.status, "created_at": model.created_at}

    def update(self, import_id: str, **changes: Any) -> dict[str, Any]:
        with Session(self.engine) as session:
            model = session.get(ImportModel, import_id)
            if not model:
                raise KeyError(import_id)
            data = json.loads(model.payload)
            data.update(changes)
            model.status = data.get("status", model.status)
            model.payload = json.dumps(data, ensure_ascii=False, default=str)
            model.updated_at = datetime.now(timezone.utc)
            session.commit()
        result = self.get(import_id)
        assert result is not None
        return result

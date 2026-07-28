import json
from datetime import datetime, timezone
from typing import Any

from app.models.import_legacy import ImportModel
from app.repositories.imports import ImportRepository


class SQLiteImportRepository(ImportRepository):
    def __init__(self, session_factory):
        self.session_factory = session_factory

    def create(self, record: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc)
        data = dict(record)
        import_id = data.pop("import_id")
        status = data.get("status", "uploaded")
        with self.session_factory() as session:
            session.add(ImportModel(id=import_id, status=status, payload=json.dumps(data, ensure_ascii=False, default=str), created_at=now, updated_at=now))
            session.commit()

    def get(self, import_id: str) -> dict[str, Any] | None:
        with self.session_factory() as session:
            model = session.get(ImportModel, import_id)
            if not model:
                return None
            data = json.loads(model.payload)
            return {"import_id": model.id, **data, "status": model.status, "created_at": model.created_at}

    def update(self, import_id: str, **changes: Any) -> dict[str, Any]:
        with self.session_factory() as session:
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

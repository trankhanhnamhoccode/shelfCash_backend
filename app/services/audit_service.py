import json
from typing import Any
from uuid import uuid4

from app.models.audit_log import AuditLogModel


SENSITIVE_KEYS = {
    "api_key",
    "authorization",
    "password",
    "secret",
    "shelfcash_api_key",
    "token",
    "x-shelfcash-key",
}


def _safe_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if str(key).lower() in SENSITIVE_KEYS else _safe_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_safe_value(item) for item in value]
    return value


def _json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(_safe_value(value), ensure_ascii=False, default=str)


class AuditService:
    def __init__(self, repository):
        self.repository = repository

    def record(
        self,
        *,
        store_id: str | None,
        action: str,
        resource_type: str,
        resource_id: str,
        before: Any = None,
        after: Any = None,
        source: str,
    ) -> AuditLogModel:
        record = AuditLogModel(
            audit_log_id=str(uuid4()),
            store_id=store_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            before_json=_json(before),
            after_json=_json(after),
            source=source,
        )
        return self.repository.add(record)

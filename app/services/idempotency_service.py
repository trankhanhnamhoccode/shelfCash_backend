import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import uuid4

from app.core.exceptions import DuplicateRequestError
from app.models.idempotency import IdempotencyRecordModel


@dataclass(frozen=True)
class IdempotencyResult:
    record: IdempotencyRecordModel
    is_replay: bool


class IdempotencyService:
    def __init__(self, repository):
        self.repository = repository

    def register(
        self,
        *,
        store_id: str | None,
        endpoint: str,
        http_method: str,
        idempotency_key: str,
        request_hash: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        response_status: int | None = None,
        response_body: Any = None,
        expires_at: datetime | None = None,
    ) -> IdempotencyResult:
        existing = self.repository.get(
            store_id=store_id,
            endpoint=endpoint,
            http_method=http_method,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            if existing.request_hash != request_hash:
                raise DuplicateRequestError(
                    details={
                        "reason": "IDEMPOTENCY_KEY_REUSED",
                        "endpoint": endpoint,
                        "http_method": http_method.upper(),
                    }
                )
            return IdempotencyResult(existing, is_replay=True)

        record = IdempotencyRecordModel(
            id=str(uuid4()),
            store_id=store_id,
            endpoint=endpoint,
            http_method=http_method.upper(),
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            resource_type=resource_type,
            resource_id=resource_id,
            response_status=response_status,
            response_body_json=(
                json.dumps(response_body, ensure_ascii=False, default=str)
                if response_body is not None
                else None
            ),
            expires_at=expires_at,
        )
        return IdempotencyResult(self.repository.add(record), is_replay=False)

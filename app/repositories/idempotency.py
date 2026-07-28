from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.idempotency import IdempotencyRecordModel


class IdempotencyRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(
        self,
        *,
        store_id: str | None,
        endpoint: str,
        http_method: str,
        idempotency_key: str,
    ) -> IdempotencyRecordModel | None:
        statement = select(IdempotencyRecordModel).where(
            IdempotencyRecordModel.store_id.is_(None)
            if store_id is None
            else IdempotencyRecordModel.store_id == store_id,
            IdempotencyRecordModel.endpoint == endpoint,
            IdempotencyRecordModel.http_method == http_method.upper(),
            IdempotencyRecordModel.idempotency_key == idempotency_key,
        )
        return self.session.scalar(statement)

    def add(self, record: IdempotencyRecordModel) -> IdempotencyRecordModel:
        self.session.add(record)
        self.session.flush()
        return record

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLogModel


class AuditLogRepository:
    def __init__(self, session: Session):
        self.session = session

    def add(self, record: AuditLogModel) -> AuditLogModel:
        self.session.add(record)
        self.session.flush()
        return record

    def list_for_resource(self, resource_type: str, resource_id: str) -> list[AuditLogModel]:
        statement = (
            select(AuditLogModel)
            .where(
                AuditLogModel.resource_type == resource_type,
                AuditLogModel.resource_id == resource_id,
            )
            .order_by(AuditLogModel.created_at)
        )
        return list(self.session.scalars(statement))

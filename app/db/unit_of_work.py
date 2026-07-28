from types import TracebackType

from sqlalchemy.orm import Session

from app.repositories.audit_logs import AuditLogRepository
from app.repositories.idempotency import IdempotencyRepository
from app.repositories.stores import StoreRepository
from app.repositories.business import CatalogRepository, HistoryRepository, InventoryRepository
from app.repositories.recipes import RecipeRepository


class UnitOfWork:
    def __init__(self, session_factory):
        self.session_factory = session_factory
        self.session: Session | None = None
        self.stores: StoreRepository
        self.idempotency: IdempotencyRepository
        self.audit_logs: AuditLogRepository
        self.catalog: CatalogRepository
        self.inventory: InventoryRepository
        self.history: HistoryRepository
        self.recipes: RecipeRepository
        self._committed = False

    def __enter__(self) -> "UnitOfWork":
        self.session = self.session_factory()
        self.stores = StoreRepository(self.session)
        self.idempotency = IdempotencyRepository(self.session)
        self.audit_logs = AuditLogRepository(self.session)
        self.catalog = CatalogRepository(self.session)
        self.inventory = InventoryRepository(self.session)
        self.history = HistoryRepository(self.session)
        self.recipes = RecipeRepository(self.session)
        return self

    def commit(self) -> None:
        if self.session is None:
            raise RuntimeError("UnitOfWork has not been entered")
        self.session.commit()
        self._committed = True

    def rollback(self) -> None:
        if self.session is not None:
            self.session.rollback()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self.session is None:
            return
        if exc_type is not None or not self._committed:
            self.session.rollback()
        self.session.close()

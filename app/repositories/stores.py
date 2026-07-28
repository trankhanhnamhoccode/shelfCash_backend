from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import StoreNotFoundError
from app.models.store import StoreModel


class StoreRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, store_id: str) -> StoreModel | None:
        return self.session.get(StoreModel, store_id)

    def get_required(self, store_id: str) -> StoreModel:
        store = self.get(store_id)
        if store is None:
            raise StoreNotFoundError(store_id)
        return store

    def exists(self, store_id: str) -> bool:
        return self.get(store_id) is not None

    def add(self, store: StoreModel) -> StoreModel:
        self.session.add(store)
        return store

    def list(self) -> list[StoreModel]:
        return list(self.session.scalars(select(StoreModel).order_by(StoreModel.store_id)))

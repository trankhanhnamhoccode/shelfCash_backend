from app.config import get_settings
from app.db.session import create_engine_from_settings, create_session_factory
from app.db.unit_of_work import UnitOfWork
from app.models.store import StoreModel


SEED_STORES = (
    {
        "store_id": "STORE_001",
        "store_name": "Cửa hàng Demo ShelfCash",
        "timezone": "Asia/Ho_Chi_Minh",
        "currency": "VND",
    },
    {
        "store_id": "STORE_TEST_001",
        "store_name": "Cửa hàng Test ShelfCash",
        "timezone": "Asia/Ho_Chi_Minh",
        "currency": "VND",
    },
)


def seed_database(session_factory) -> list[str]:
    created: list[str] = []
    with UnitOfWork(session_factory) as uow:
        for values in SEED_STORES:
            if not uow.stores.exists(values["store_id"]):
                uow.stores.add(StoreModel(**values))
                created.append(values["store_id"])
        uow.commit()
    return created


def main() -> None:
    settings = get_settings()
    engine = create_engine_from_settings(settings)
    try:
        created = seed_database(create_session_factory(engine))
        print(f"Seed complete. Created {len(created)} store(s): {', '.join(created) or 'none'}")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()

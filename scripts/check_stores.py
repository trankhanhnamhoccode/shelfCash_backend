from sqlalchemy import select

from app.config import get_settings
from app.db.session import (
    create_engine_from_settings,
    create_session_factory,
)
from app.models.business import StoreSettingsModel


def main() -> None:
    settings = get_settings()

    engine = create_engine_from_settings(settings)
    session_factory = create_session_factory(engine)

    with session_factory() as session:
        stores = session.scalars(
            select(StoreSettingsModel).order_by(StoreSettingsModel.store_id)
        ).all()

        if not stores:
            print("Database hiện chưa có store nào.")
            return

        print(f"Tìm thấy {len(stores)} store:")

        for store in stores:
            print(
                f"- store_id={store.store_id}, "
                f"name={store.store_name}, "
                f"timezone={store.timezone}, "
                f"currency={store.currency}"
            )


if __name__ == "__main__":
    main()
from sqlalchemy import text

from app.core.exceptions import DatabaseNotReadyError


def check_database(session_factory) -> None:
    try:
        with session_factory() as session:
            session.execute(text("SELECT 1"))
    except Exception as exc:
        raise DatabaseNotReadyError() from exc

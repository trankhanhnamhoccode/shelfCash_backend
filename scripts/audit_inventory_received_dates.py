"""Read-only received-date provenance audit for live data.

Usage:
    python scripts/audit_inventory_received_dates.py
    python scripts/audit_inventory_received_dates.py --database-url sqlite:///runtime/shelfcash.db --json

This command never updates data.  It intentionally reports provenance status
instead of trying to infer a historical receipt date from a snapshot/import
timestamp.
"""

from __future__ import annotations

import argparse
import json

from sqlalchemy import func, select

from app.config import get_settings
from app.db.session import create_engine_from_url, create_session_factory
from app.models.business import InventoryLotModel


def _count(session, *conditions) -> int:
    statement = select(func.count()).select_from(InventoryLotModel)
    if conditions:
        statement = statement.where(*conditions)
    return int(session.scalar(statement) or 0)


def audit(database_url: str) -> dict[str, int | str]:
    engine = create_engine_from_url(database_url)
    factory = create_session_factory(engine)
    try:
        with factory() as session:
            definitely_legacy_fallback = _count(
                session,
                InventoryLotModel.received_date_status == "legacy_unknown",
                InventoryLotModel.source == "import",
                InventoryLotModel.received_date.is_(None),
            )
            suspicious = _count(
                session,
                InventoryLotModel.received_date_status == "legacy_unknown",
                InventoryLotModel.received_date.is_not(None),
            )
            cannot_determine = _count(
                session,
                InventoryLotModel.received_date_status == "legacy_unknown",
                InventoryLotModel.received_date.is_(None),
                InventoryLotModel.source != "import",
            )
            return {
                "mode": "dry_run",
                "declared": _count(session, InventoryLotModel.received_date_status == "declared"),
                "unknown": _count(session, InventoryLotModel.received_date_status == "unknown"),
                "definitely_legacy_fallback": definitely_legacy_fallback,
                "suspicious": suspicious,
                "cannot_determine": cannot_determine,
                "total": _count(session),
            }
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only inventory received-date provenance audit.")
    parser.add_argument("--database-url", default=get_settings().database_url)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    result = audit(args.database_url)
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print("Inventory received-date audit (dry run)")
        for key, value in result.items():
            print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

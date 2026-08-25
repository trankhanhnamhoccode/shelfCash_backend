"""Read-only inventory receipt-date audit.

Usage: python scripts/audit_inventory_received_dates.py --json
Uses DATABASE_URL when set, otherwise the application's configured database.
"""
import argparse
import json
import sys
from pathlib import Path

# ``python scripts/audit_inventory_received_dates.py`` sets sys.path to the
# scripts directory, not the repository root.  Keep the documented invocation
# usable without requiring callers to set PYTHONPATH.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.config import Settings
from app.db.session import create_engine_from_url, create_session_factory
from app.models.business import InventoryLotModel, SupplierIngredientTermModel


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--json",action="store_true")
    args=parser.parse_args()
    settings=Settings()
    engine=create_engine_from_url(settings.database_url)
    factory=create_session_factory(engine)
    with factory() as session:
        lots=list(session.scalars(select(InventoryLotModel)))
        categories={"declared":[],"provably_legacy_fallback":[],"suspicious":[],"unverifiable":[]}
        for lot in lots:
            if lot.received_date_status=="declared": category="declared"
            elif lot.source=="import": category="provably_legacy_fallback"
            elif lot.received_date is None: category="unverifiable"
            else: category="suspicious"
            categories[category].append({"lot_id":lot.lot_id,"store_id":lot.store_id,"ingredient_id":lot.ingredient_id,"batch_id":lot.batch_code,"received_date":str(lot.received_date) if lot.received_date else None,"status":lot.received_date_status,"source":lot.source})
        terms=[{"constraint_id":term.constraint_id,"store_id":term.store_id,"ingredient_id":term.ingredient_id,"unit_cost":term.unit_cost,"lead_time_days":term.lead_time_days} for term in session.scalars(select(SupplierIngredientTermModel).where((SupplierIngredientTermModel.unit_cost==0)|(SupplierIngredientTermModel.lead_time_days==0)))]
    payload={"lots":categories,"supplier_terms_requiring_manual_authority_review":terms}
    print(json.dumps(payload,ensure_ascii=False,default=str) if args.json else json.dumps({key:len(value) for key,value in categories.items()},ensure_ascii=False))
    engine.dispose()


if __name__=="__main__":
    main()

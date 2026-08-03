from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.db.session import create_engine_from_settings, create_session_factory
from app.schemas.forecast import ForecastTrainRequest
from app.services.forecast_service import ForecastService


def main() -> int:
    parser = argparse.ArgumentParser(description="Train ShelfCash Forecast Core from persisted database data")
    parser.add_argument("--store-id", required=True)
    parser.add_argument("--cutoff-date", required=True, type=date.fromisoformat)
    parser.add_argument("--model-version")
    parser.add_argument("--history-days", type=int)
    args = parser.parse_args()
    settings = get_settings(); settings.forecast_artifact_root.mkdir(parents=True, exist_ok=True)
    engine = create_engine_from_settings(settings)
    try:
        result = ForecastService(create_session_factory(engine), settings).train(ForecastTrainRequest(
            store_id=args.store_id, cutoff_date=args.cutoff_date,
            model_version=args.model_version, history_days=args.history_days))
        print(json.dumps({"store_id": result["store_id"], "model_version": result["model_version"],
                          "status": result["status"], "trained_at": str(result["trained_at"]),
                          "history_start": str(result["history_start"]), "history_end": str(result["history_end"])}, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(f"Forecast training failed: {exc}", file=sys.stderr); return 1
    finally: engine.dispose()


if __name__ == "__main__": raise SystemExit(main())

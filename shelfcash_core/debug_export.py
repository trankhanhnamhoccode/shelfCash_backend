"""Side-effect-free CSV export for forecast model inputs.

This module deliberately serializes frames supplied by the forecast pipeline; it
does not know about, or read from, the database.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path

import pandas as pd


logger = logging.getLogger("shelfcash.forecast.debug")


@dataclass(frozen=True)
class ForecastDebugExport:
    """Run-scoped configuration for an optional forecast feature export."""

    enabled: bool = False
    output_directory: Path = Path("forecast_debug")
    run_id: str = "forecast"
    filename: str = "forecast_features.csv"


def export_forecast_features_csv(
    model_input: pd.DataFrame,
    output_directory: str | Path,
    *,
    identifiers: pd.DataFrame | None = None,
    target: pd.Series | None = None,
    filename: str = "forecast_features.csv",
) -> Path:
    """Write an already-computed model input without changing it.

    Identifier and target columns are copied only into the CSV representation;
    model feature order is retained exactly as supplied by ``model_input``.
    """

    if identifiers is not None and len(identifiers) != len(model_input):
        raise ValueError("Identifiers must have the same row count as model_input.")
    if target is not None and len(target) != len(model_input):
        raise ValueError("Target must have the same row count as model_input.")

    export_parts: list[pd.DataFrame] = []
    if identifiers is not None:
        export_parts.append(identifiers.reset_index(drop=True).copy(deep=True))
    if target is not None:
        export_parts.append(pd.DataFrame({"target": target.reset_index(drop=True)}))
    export_parts.append(model_input.reset_index(drop=True).copy(deep=True))
    export_frame = pd.concat(export_parts, axis=1)

    path = Path(output_directory) / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    export_frame.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def export_if_enabled(
    debug_export: ForecastDebugExport | None,
    model_input: pd.DataFrame,
    *,
    identifiers: pd.DataFrame | None = None,
    target: pd.Series | None = None,
) -> Path | None:
    """Best-effort wrapper so debug filesystem errors never fail forecasting."""

    if debug_export is None or not debug_export.enabled:
        return None
    try:
        path = export_forecast_features_csv(
            model_input,
            Path(debug_export.output_directory) / debug_export.run_id,
            identifiers=identifiers,
            target=target,
            filename=debug_export.filename,
        )
        logger.info(
            "Forecast debug export: path=%s rows=%s columns=%s",
            path,
            len(model_input),
            len((identifiers.columns if identifiers is not None else []))
            + (1 if target is not None else 0)
            + len(model_input.columns),
        )
        return path
    except Exception as exc:  # Debug I/O must not change forecast availability.
        logger.warning("Forecast debug export failed: %s", exc, exc_info=True)
        return None

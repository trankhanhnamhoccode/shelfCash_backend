from __future__ import annotations

import numpy as np
import pandas as pd

from shelfcash_core.debug_export import ForecastDebugExport, export_if_enabled
from shelfcash_core.models.quantile_models import QuantileModelBundle


def predict_raw_quantiles(
    bundle: QuantileModelBundle,
    frame: pd.DataFrame,
    debug_export: ForecastDebugExport | None = None,
) -> pd.DataFrame:
    result = frame.copy()
    x = result[bundle.feature_names]
    identifier_columns = [
        column
        for column in ("target_date", "store_key", "product_key", "product_name", "unit")
        if column in result
    ]
    export_if_enabled(
        debug_export,
        x,
        identifiers=result.loc[:, identifier_columns],
    )
    result["p25_raw"] = np.maximum(0.0, bundle.models[0.25].predict(x))
    result["p50_raw"] = np.maximum(0.0, bundle.models[0.50].predict(x))
    result["p75_raw"] = np.maximum(0.0, bundle.models[0.75].predict(x))
    return result

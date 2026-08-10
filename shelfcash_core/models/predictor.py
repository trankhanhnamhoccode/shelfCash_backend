from __future__ import annotations

import numpy as np
import pandas as pd

from shelfcash_core.models.quantile_models import QuantileModelBundle


def predict_raw_quantiles(
    bundle: QuantileModelBundle,
    frame: pd.DataFrame,
) -> pd.DataFrame:
    result = frame.copy()
    x = result[bundle.feature_names]
    result["p25_raw"] = np.maximum(0.0, bundle.models[0.25].predict(x))
    result["p50_raw"] = np.maximum(0.0, bundle.models[0.50].predict(x))
    result["p75_raw"] = np.maximum(0.0, bundle.models[0.75].predict(x))
    return result

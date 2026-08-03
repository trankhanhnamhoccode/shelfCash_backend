from __future__ import annotations

import pandas as pd


def seasonal_naive_predict(frame: pd.DataFrame) -> pd.Series:
    prediction = frame["seasonal_lag_7_target"].copy()
    prediction = prediction.fillna(frame["rolling_median_7"])
    prediction = prediction.fillna(frame["rolling_median_28"])
    prediction = prediction.fillna(frame["last_observed_demand"])
    return prediction.fillna(0.0).clip(lower=0).astype(float)

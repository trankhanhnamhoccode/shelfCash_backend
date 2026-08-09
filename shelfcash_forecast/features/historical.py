from __future__ import annotations

import pandas as pd

from shelfcash_forecast.config import ForecastConfig


GROUP_COLUMNS = ["store_key", "product_key"]


def _rolling_transform(
    frame: pd.DataFrame,
    column: str,
    window: int,
    statistic: str,
    minimum_periods: int,
) -> pd.Series:
    grouped = frame.groupby(GROUP_COLUMNS, observed=True)[column]

    def calculate(series: pd.Series) -> pd.Series:
        rolling = series.rolling(window=window, min_periods=minimum_periods)
        return getattr(rolling, statistic)()

    return grouped.transform(calculate)


def add_historical_features(
    panel: pd.DataFrame,
    config: ForecastConfig,
) -> pd.DataFrame:
    frame = panel.sort_values(GROUP_COLUMNS + ["date"]).copy()
    grouped = frame.groupby(GROUP_COLUMNS, observed=True)

    frame["last_observed_demand"] = grouped["demand_proxy"].ffill()
    frame["last_observed_price"] = grouped["selling_price"].ffill()
    frame["price_lag_1"] = grouped["selling_price"].shift(1).ffill()
    frame["history_observation_count"] = grouped["demand_proxy"].transform(
        lambda series: series.notna().cumsum()
    )

    for lag in config.lag_days:
        frame[f"cutoff_lag_{lag}"] = grouped["demand_proxy"].shift(lag)

    for window in config.rolling_windows:
        minimum_mean = max(2, window // 2)
        minimum_std = max(3, window // 2)
        frame[f"rolling_mean_{window}"] = _rolling_transform(
            frame, "demand_proxy", window, "mean", minimum_mean
        )
        frame[f"rolling_median_{window}"] = _rolling_transform(
            frame, "demand_proxy", window, "median", minimum_mean
        )
        frame[f"rolling_std_{window}"] = _rolling_transform(
            frame, "demand_proxy", window, "std", minimum_std
        )

    previous_seven_mean = grouped["demand_proxy"].transform(
        lambda series: series.shift(7).rolling(7, min_periods=4).mean()
    )
    frame["mean_last_7_minus_previous_7"] = (
        frame["rolling_mean_7"] - previous_seven_mean
    )

    stockout_numeric = frame["effective_is_stockout"].astype("Float64")
    frame["_stockout_numeric"] = stockout_numeric
    for window in (7, 28):
        frame[f"stockout_count_{window}"] = _rolling_transform(
            frame, "_stockout_numeric", window, "sum", 1
        )
        frame[f"stockout_rate_{window}"] = _rolling_transform(
            frame, "_stockout_numeric", window, "mean", 1
        )
    frame = frame.drop(columns="_stockout_numeric")

    return frame

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
from statsmodels.tsa.holtwinters import ExponentialSmoothing


def forecast_ets_path(
    history: pd.Series,
    maximum_horizon: int,
) -> np.ndarray | None:
    clean = history.dropna().astype(float)
    if len(clean) < 28:
        return None

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model = ExponentialSmoothing(
                clean.to_numpy(),
                trend="add",
                seasonal="add",
                seasonal_periods=7,
                initialization_method="estimated",
            )
            fitted = model.fit(optimized=True)
            prediction = np.asarray(fitted.forecast(maximum_horizon), dtype=float)
        return np.maximum(0.0, prediction)
    except (ValueError, RuntimeError, FloatingPointError, OverflowError):
        return None


def forecast_ets_series(history: pd.Series, horizon: int) -> float | None:
    path = forecast_ets_path(history, horizon)
    return None if path is None else float(path[horizon - 1])


def ets_predict_rows(
    rows: pd.DataFrame,
    panel: pd.DataFrame,
    fallback: pd.Series,
) -> pd.Series:
    """ETS baseline with one fit per store-product-cutoff and horizon-path caching."""

    indexed_panel = panel.sort_values("date")
    maximum_horizon = int(rows["horizon"].max())
    cache: dict[tuple[str, str, pd.Timestamp], np.ndarray | None] = {}
    output: list[float] = []

    for position, row in enumerate(rows.itertuples(index=False)):
        key = (
            str(row.store_key),
            str(row.product_key),
            pd.Timestamp(row.cutoff_date),
        )
        if key not in cache:
            history = indexed_panel.loc[
                indexed_panel["store_key"].eq(row.store_key)
                & indexed_panel["product_key"].eq(row.product_key)
                & indexed_panel["date"].le(row.cutoff_date),
                "demand_proxy",
            ]
            cache[key] = forecast_ets_path(history, maximum_horizon)

        path = cache[key]
        value = None if path is None else float(path[int(row.horizon) - 1])
        output.append(float(fallback.iloc[position]) if value is None else value)

    return pd.Series(output, index=rows.index, dtype=float)

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def pinball_loss(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    quantile: float,
) -> float:
    error = y_true - y_pred
    losses = np.maximum(quantile * error, (quantile - 1.0) * error)
    return float(np.mean(losses))


def mae(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    return float(np.mean(np.abs(y_true - y_pred)))


def wape(y_true: np.ndarray, y_pred: np.ndarray) -> float | None:
    denominator = float(np.sum(np.abs(y_true)))
    if denominator == 0:
        return None
    return float(np.sum(np.abs(y_true - y_pred)) / denominator)


def evaluate_quantile_predictions(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {}
    y_true = frame["target"].to_numpy(dtype=float)
    return {
        "row_count": int(len(frame)),
        "pinball_q25": pinball_loss(y_true, frame["p25"].to_numpy(dtype=float), 0.25),
        "pinball_q50": pinball_loss(y_true, frame["p50"].to_numpy(dtype=float), 0.50),
        "pinball_q75": pinball_loss(y_true, frame["p75"].to_numpy(dtype=float), 0.75),
        "mae_p50": mae(y_true, frame["p50"].to_numpy(dtype=float)),
        "wape_p50": wape(y_true, frame["p50"].to_numpy(dtype=float)),
    }


def evaluate_point_prediction(
    frame: pd.DataFrame,
    prediction_column: str,
) -> dict[str, Any]:
    if frame.empty:
        return {}
    y_true = frame["target"].to_numpy(dtype=float)
    prediction = frame[prediction_column].to_numpy(dtype=float)
    return {
        "row_count": int(len(frame)),
        "mae": mae(y_true, prediction),
        "wape": wape(y_true, prediction),
    }


def metric_breakdown_by_horizon(frame: pd.DataFrame) -> dict[str, dict[str, Any]]:
    return {
        str(int(horizon)): evaluate_quantile_predictions(group)
        for horizon, group in frame.groupby("horizon", observed=True)
    }

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import lightgbm as lgb
import numpy as np
import pandas as pd


class PredictableModel(Protocol):
    def predict(self, data: pd.DataFrame) -> np.ndarray: ...


@dataclass
class QuantileModelBundle:
    models: dict[float, PredictableModel]
    feature_names: list[str]
    categorical_features: list[str]


def build_quantile_model(
    quantile: float,
    base_params: dict[str, object],
) -> lgb.LGBMRegressor:
    return lgb.LGBMRegressor(
        objective="quantile",
        alpha=quantile,
        **base_params,
    )


def train_quantile_models(
    train: pd.DataFrame,
    feature_names: list[str],
    categorical_features: list[str],
    quantiles: tuple[float, ...],
    base_params: dict[str, object],
) -> QuantileModelBundle:
    x_train = train[feature_names]
    y_train = train["target"].astype(float)
    models: dict[float, PredictableModel] = {}

    for quantile in quantiles:
        model = build_quantile_model(quantile, base_params)
        model.fit(
            x_train,
            y_train,
            categorical_feature=categorical_features,
        )
        models[quantile] = model

    return QuantileModelBundle(
        models=models,
        feature_names=feature_names,
        categorical_features=categorical_features,
    )

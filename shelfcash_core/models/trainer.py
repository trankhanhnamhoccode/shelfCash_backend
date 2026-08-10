from __future__ import annotations

import pandas as pd

from shelfcash_core.config import ForecastConfig
from shelfcash_core.features.specification import (
    CATEGORICAL_MODEL_COLUMNS,
    MODEL_FEATURES,
)
from shelfcash_core.models.quantile_models import (
    QuantileModelBundle,
    train_quantile_models,
)


def train_model_bundle(
    train: pd.DataFrame,
    config: ForecastConfig,
) -> QuantileModelBundle:
    return train_quantile_models(
        train=train,
        feature_names=list(MODEL_FEATURES),
        categorical_features=list(CATEGORICAL_MODEL_COLUMNS),
        quantiles=config.quantiles,
        base_params=config.lightgbm_params,
    )

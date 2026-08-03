from __future__ import annotations

from typing import Any

import pandas as pd

from shelfcash_forecast.baselines.seasonal_naive import seasonal_naive_predict
from shelfcash_forecast.calibration.crossing import correct_quantile_crossing
from shelfcash_forecast.config import ForecastConfig
from shelfcash_forecast.evaluation.metrics import (
    evaluate_point_prediction,
    evaluate_quantile_predictions,
)
from shelfcash_forecast.evaluation.splits import generate_walk_forward_folds
from shelfcash_forecast.features.specification import CategoryEncoder
from shelfcash_forecast.models.predictor import predict_raw_quantiles
from shelfcash_forecast.models.trainer import train_model_bundle


def run_walk_forward(
    training_period: pd.DataFrame,
    config: ForecastConfig,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    folds = generate_walk_forward_folds(
        training_period["target_date"],
        minimum_train_days=config.walk_forward_minimum_train_days,
        validation_days=config.walk_forward_validation_days,
        step_days=config.walk_forward_step_days,
        maximum_folds=config.walk_forward_maximum_folds,
    )
    if not folds:
        return pd.DataFrame(), {"status": "skipped", "reason": "INSUFFICIENT_FOLD_DATES"}

    predictions: list[pd.DataFrame] = []
    for fold in folds:
        train_fold = training_period.loc[
            training_period["target_date"].le(fold.train_end)
            & training_period["target_train_eligible"].astype("boolean").fillna(False)
            & training_period["target"].notna()
            & training_period["history_observation_count"].ge(
                config.minimum_history_observations
            )
        ].copy()
        valid_fold = training_period.loc[
            training_period["target_date"].between(
                fold.validation_start, fold.validation_end
            )
            & training_period["target"].notna()
            & training_period["history_observation_count"].ge(
                config.minimum_history_observations
            )
        ].copy()
        if train_fold.empty or valid_fold.empty:
            continue

        encoder = CategoryEncoder.fit(train_fold)
        encoded_train = encoder.transform(train_fold)
        encoded_valid = encoder.transform(valid_fold)
        bundle = train_model_bundle(encoded_train, config)
        fold_prediction = correct_quantile_crossing(
            predict_raw_quantiles(bundle, encoded_valid)
        )
        fold_prediction["baseline_p50"] = seasonal_naive_predict(valid_fold).to_numpy()
        fold_prediction["fold_id"] = fold.fold_id
        predictions.append(fold_prediction)

    if not predictions:
        return pd.DataFrame(), {"status": "skipped", "reason": "EMPTY_FOLDS"}

    combined = pd.concat(predictions, ignore_index=True)
    metrics = {
        "status": "completed",
        "fold_count": int(combined["fold_id"].nunique()),
        "lightgbm": evaluate_quantile_predictions(combined),
        "seasonal_naive": evaluate_point_prediction(combined, "baseline_p50"),
        "by_fold": {
            str(int(fold_id)): evaluate_quantile_predictions(group)
            for fold_id, group in combined.groupby("fold_id", observed=True)
        },
    }
    return combined, metrics

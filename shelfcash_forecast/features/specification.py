from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


CATEGORICAL_SOURCE_COLUMNS = ["store_key", "product_key"]
CATEGORICAL_MODEL_COLUMNS = ["store_code", "product_code"]

NUMERIC_FEATURES = [
    "horizon",
    "history_observation_count",
    "last_observed_demand",
    "cutoff_lag_1",
    "cutoff_lag_2",
    "cutoff_lag_7",
    "cutoff_lag_14",
    "cutoff_lag_28",
    "rolling_mean_7",
    "rolling_mean_14",
    "rolling_mean_28",
    "rolling_median_7",
    "rolling_median_14",
    "rolling_median_28",
    "rolling_std_7",
    "rolling_std_14",
    "rolling_std_28",
    "mean_last_7_minus_previous_7",
    "stockout_count_7",
    "stockout_count_28",
    "stockout_rate_7",
    "stockout_rate_28",
    "seasonal_lag_7_target",
    "seasonal_lag_14_target",
    "seasonal_lag_28_target",
    "target_day_of_week",
    "target_is_weekend",
    "target_month",
    "target_day_of_month",
    "target_week_of_month",
    "target_week_of_year",
    "target_is_holiday",
    "target_store_closed",
    "target_temperature",
    "target_rainfall",
    "calendar_available",
]

MODEL_FEATURES = CATEGORICAL_MODEL_COLUMNS + NUMERIC_FEATURES


@dataclass
class CategoryEncoder:
    mappings: dict[str, dict[str, int]]

    @classmethod
    def fit(cls, frame: pd.DataFrame) -> "CategoryEncoder":
        mappings: dict[str, dict[str, int]] = {}
        for source in CATEGORICAL_SOURCE_COLUMNS:
            values = (
                frame[source]
                .astype("string")
                .fillna("__MISSING__")
                .drop_duplicates()
                .tolist()
            )
            mappings[source] = {
                value: index for index, value in enumerate(sorted(values))
            }
        return cls(mappings=mappings)

    def transform(self, frame: pd.DataFrame) -> pd.DataFrame:
        result = frame.copy()
        for source, output in zip(
            CATEGORICAL_SOURCE_COLUMNS,
            CATEGORICAL_MODEL_COLUMNS,
            strict=True,
        ):
            mapping = self.mappings[source]
            result[output] = (
                result[source]
                .astype("string")
                .fillna("__MISSING__")
                .map(mapping)
                .fillna(-1)
                .astype("int32")
            )
        return result

    def to_dict(self) -> dict[str, dict[str, int]]:
        return self.mappings

    @classmethod
    def from_dict(cls, payload: dict[str, dict[str, int]]) -> "CategoryEncoder":
        return cls(
            mappings={
                column: {str(key): int(value) for key, value in mapping.items()}
                for column, mapping in payload.items()
            }
        )

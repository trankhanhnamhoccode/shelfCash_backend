from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class ForecastConfig:
    """Configuration shared by training and inference."""

    horizons: tuple[int, ...] = (1, 2, 3, 4, 5, 6, 7)
    quantiles: tuple[float, ...] = (0.25, 0.50, 0.75)

    minimum_history_observations: int = 28
    calibration_days: int = 28
    test_days: int = 28

    nominal_coverage: float = 0.50
    minimum_calibration_samples: int = 20

    lag_days: tuple[int, ...] = (1, 2, 7, 14, 28)
    rolling_windows: tuple[int, ...] = (7, 14, 28)
    target_seasonal_lags: tuple[int, ...] = (7, 14, 28)

    walk_forward_minimum_train_days: int = 84
    walk_forward_validation_days: int = 14
    walk_forward_step_days: int = 14
    walk_forward_maximum_folds: int = 3

    random_seed: int = 42
    default_store_key: str = "STORE_DEFAULT"

    lightgbm_params: dict[str, Any] = field(
        default_factory=lambda: {
            "learning_rate": 0.04,
            "n_estimators": 350,
            "num_leaves": 31,
            "min_child_samples": 20,
            "subsample": 0.9,
            "subsample_freq": 1,
            "colsample_bytree": 0.9,
            "reg_alpha": 0.05,
            "reg_lambda": 0.5,
            "random_state": 42,
            "n_jobs": -1,
            "verbosity": -1,
        }
    )

    def __post_init__(self) -> None:
        if not self.horizons or any(horizon < 1 for horizon in self.horizons):
            raise ValueError("horizons phải chứa các số nguyên dương.")
        if tuple(sorted(set(self.horizons))) != self.horizons:
            raise ValueError("horizons phải tăng dần và không trùng nhau.")
        if self.quantiles != (0.25, 0.50, 0.75):
            raise ValueError("Forecast Core v0.1 chỉ hỗ trợ P25/P50/P75.")
        if not 0 < self.nominal_coverage < 1:
            raise ValueError("nominal_coverage phải nằm trong (0, 1).")
        if self.minimum_history_observations < max(self.rolling_windows):
            raise ValueError(
                "minimum_history_observations phải >= rolling window lớn nhất."
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ForecastConfig":
        data = dict(payload)
        tuple_fields = {
            "horizons",
            "quantiles",
            "lag_days",
            "rolling_windows",
            "target_seasonal_lags",
        }
        for field_name in tuple_fields:
            if field_name in data:
                data[field_name] = tuple(data[field_name])
        return cls(**data)

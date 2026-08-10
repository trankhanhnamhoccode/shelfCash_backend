from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from shelfcash_core.exceptions import InsufficientDataError


@dataclass(frozen=True)
class DateSplit:
    train_end: pd.Timestamp
    calibration_start: pd.Timestamp
    calibration_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp

    def to_dict(self) -> dict[str, str]:
        return {
            "train_end": self.train_end.date().isoformat(),
            "calibration_start": self.calibration_start.date().isoformat(),
            "calibration_end": self.calibration_end.date().isoformat(),
            "test_start": self.test_start.date().isoformat(),
            "test_end": self.test_end.date().isoformat(),
        }


@dataclass(frozen=True)
class WalkForwardFold:
    fold_id: int
    train_end: pd.Timestamp
    validation_start: pd.Timestamp
    validation_end: pd.Timestamp


def make_final_split(
    table: pd.DataFrame,
    calibration_days: int,
    test_days: int,
    minimum_train_dates: int,
) -> DateSplit:
    dates = pd.DatetimeIndex(sorted(pd.to_datetime(table["target_date"]).dropna().unique()))
    required = minimum_train_dates + calibration_days + test_days
    if len(dates) < required:
        raise InsufficientDataError(
            f"Cần ít nhất {required} target dates để split, hiện có {len(dates)}. "
            "Hãy giảm calibration/test window trong ForecastConfig hoặc bổ sung lịch sử."
        )

    test_start_index = len(dates) - test_days
    calibration_start_index = test_start_index - calibration_days
    return DateSplit(
        train_end=pd.Timestamp(dates[calibration_start_index - 1]),
        calibration_start=pd.Timestamp(dates[calibration_start_index]),
        calibration_end=pd.Timestamp(dates[test_start_index - 1]),
        test_start=pd.Timestamp(dates[test_start_index]),
        test_end=pd.Timestamp(dates[-1]),
    )


def apply_final_split(
    table: pd.DataFrame,
    split: DateSplit,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    train = table.loc[table["target_date"].le(split.train_end)].copy()
    calibration = table.loc[
        table["target_date"].between(split.calibration_start, split.calibration_end)
    ].copy()
    test = table.loc[table["target_date"].between(split.test_start, split.test_end)].copy()
    return train, calibration, test


def generate_walk_forward_folds(
    target_dates: pd.Series,
    minimum_train_days: int,
    validation_days: int,
    step_days: int,
    maximum_folds: int,
) -> list[WalkForwardFold]:
    dates = pd.DatetimeIndex(sorted(pd.to_datetime(target_dates).dropna().unique()))
    if len(dates) <= minimum_train_days:
        return []

    folds: list[WalkForwardFold] = []
    validation_start_index = minimum_train_days
    fold_id = 1
    while validation_start_index < len(dates):
        validation_end_index = min(
            validation_start_index + validation_days - 1,
            len(dates) - 1,
        )
        folds.append(
            WalkForwardFold(
                fold_id=fold_id,
                train_end=pd.Timestamp(dates[validation_start_index - 1]),
                validation_start=pd.Timestamp(dates[validation_start_index]),
                validation_end=pd.Timestamp(dates[validation_end_index]),
            )
        )
        validation_start_index += step_days
        fold_id += 1
    return folds[-maximum_folds:]

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from shelfcash_core.exceptions import InsufficientDataError


@dataclass(frozen=True)
class CalibrationValue:
    correction: float
    sample_count: int
    quantile_level: float
    source: str


@dataclass
class CQRCalibrator:
    desired_coverage: float
    minimum_samples: int
    by_horizon: dict[int, CalibrationValue]
    global_value: CalibrationValue

    def to_dict(self) -> dict[str, object]:
        return {
            "desired_coverage": self.desired_coverage,
            "minimum_samples": self.minimum_samples,
            "by_horizon": {
                str(horizon): asdict(value)
                for horizon, value in self.by_horizon.items()
            },
            "global_value": asdict(self.global_value),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "CQRCalibrator":
        by_horizon_payload = payload.get("by_horizon", {})
        if not isinstance(by_horizon_payload, dict):
            raise ValueError("by_horizon trong calibrator không hợp lệ.")
        global_payload = payload["global_value"]
        if not isinstance(global_payload, dict):
            raise ValueError("global_value trong calibrator không hợp lệ.")
        return cls(
            desired_coverage=float(payload["desired_coverage"]),
            minimum_samples=int(payload["minimum_samples"]),
            by_horizon={
                int(horizon): CalibrationValue(**value)
                for horizon, value in by_horizon_payload.items()
            },
            global_value=CalibrationValue(**global_payload),
        )


def nonconformity_scores(frame: pd.DataFrame) -> np.ndarray:
    actual = frame["target"].to_numpy(dtype=float)
    lower = frame["p25"].to_numpy(dtype=float)
    upper = frame["p75"].to_numpy(dtype=float)
    return np.maximum(lower - actual, actual - upper)


def conformal_quantile(
    scores: np.ndarray, # calibration scores, đo actual nằm ở đâu so với interval [P25, P75]
    desired_coverage: float, # mức coverage mong muốn, ví dụ 0.5
) -> tuple[float, float]:
    clean = np.asarray(scores, dtype=float)
    clean = clean[np.isfinite(clean)]
    if len(clean) == 0:
        raise InsufficientDataError("Không có calibration score hợp lệ.")

    level = math.ceil((len(clean) + 1) * desired_coverage) / len(clean) # mức quantile thực tế được dùng để tính correction này, ví dụ 0.5, 0.75, 0.9
    level = min(1.0, max(0.0, level)) # đảm bảo level nằm trong khoảng [0, 1]
    correction = float(np.quantile(clean, level, method="higher")) # tính index trung vị ( giữa all scores ) rồi lấy higher 
    return correction, level


def fit_cqr_calibrator(
    calibration_predictions: pd.DataFrame, # target ,p25 ,p50 ,p75 ,horizon
    desired_coverage: float,
    minimum_samples: int,
) -> CQRCalibrator:
    frame = calibration_predictions.dropna(
        subset=["target", "p25", "p75", "horizon"]
    ).copy()
    if frame.empty:
        raise InsufficientDataError("Calibration set không có prediction hợp lệ.")

    global_scores = nonconformity_scores(frame) # Hàm đo actual nằm ở đâu so với interval [P25, P75].
    global_correction, global_level = conformal_quantile(
        global_scores, desired_coverage
    )
    global_value = CalibrationValue( # Mỗi object lưu kết quả calibration cho một nhóm.
        correction=global_correction, # số đơn vị cần mở rộng hoặc thu hẹp khoảng P25–P75 để coverage thực tế gần mức mong muốn.
        sample_count=len(global_scores), # số lượng calibration rows được dùng để tính toán correction này.
        quantile_level=global_level, # mức quantile thực tế được dùng để tính correction này.
        source="global", # nguồn correction này là global, không phải theo horizon.
    )

    by_horizon: dict[int, CalibrationValue] = {}
    for horizon, group in frame.groupby("horizon", observed=True):
        scores = nonconformity_scores(group)
        if len(scores) < minimum_samples:
            continue
        correction, level = conformal_quantile(scores, desired_coverage)
        by_horizon[int(horizon)] = CalibrationValue(
            correction=correction,
            sample_count=len(scores),
            quantile_level=level,
            source=f"horizon_{int(horizon)}",
        )

    return CQRCalibrator(
        desired_coverage=desired_coverage,
        minimum_samples=minimum_samples,
        by_horizon=by_horizon, # mỗi horizon có correction riêng
        global_value=global_value, # correction chung cho tất cả horizon
    )


def apply_cqr_calibrator(
    frame: pd.DataFrame,
    calibrator: CQRCalibrator,
) -> pd.DataFrame:
    result = frame.copy()
    values = [
        calibrator.by_horizon.get(int(horizon), calibrator.global_value)
        for horizon in result["horizon"]
    ]
    result["calibration_correction"] = [value.correction for value in values]
    result["calibration_source"] = [value.source for value in values]
    result["interval_lower"] = np.maximum(
        0.0, result["p25"] - result["calibration_correction"]
    )
    result["interval_upper"] = (
        result["p75"] + result["calibration_correction"]
    )
    # Decision interval luôn phải chứa median forecast; dùng midpoint nếu caller
    # chỉ cung cấp lower/upper quantiles.
    median = (result["p25"] + result["p75"]) / 2.0
    if "p50" in result.columns:
        median = result["p50"]
    result["interval_lower"] = np.minimum(result["interval_lower"], median)
    result["interval_upper"] = np.maximum(result["interval_upper"], median)

    invalid = result["interval_lower"] > result["interval_upper"]
    if invalid.any():
        midpoint = (
            result.loc[invalid, "interval_lower"]
            + result.loc[invalid, "interval_upper"]
        ) / 2.0
        result.loc[invalid, "interval_lower"] = midpoint.clip(lower=0)
        result.loc[invalid, "interval_upper"] = midpoint.clip(lower=0)
    return result

from __future__ import annotations

import math
from collections import Counter
from statistics import fmean

from app.forecasting.contracts import (
    ForecastCompatibilityReport,
    NonFinitePrediction,
    ForecastPrediction,
    ForecastPredictionResult,
    PredictionDifference,
    QuantileViolation,
)


def _key(item: ForecastPrediction) -> tuple[str, object, int]:
    return item.product_id, item.target_date, item.horizon


def _relative_difference(production: float, shadow: float) -> float | None:
    if production == 0:
        return 0.0 if shadow == 0 else None
    return abs(shadow - production) / abs(production)


def _violations(provider: str, predictions: tuple[ForecastPrediction, ...]) -> tuple[QuantileViolation, ...]:
    return tuple(
        QuantileViolation(provider, item.product_id, item.target_date, item.horizon, item.p25, item.p50, item.p75)
        for item in sorted(predictions, key=_key)
        if not item.p25 <= item.p50 <= item.p75
    )


def _duplicates(predictions: tuple[ForecastPrediction, ...]) -> tuple[tuple[str, object, int], ...]:
    counts = Counter(_key(item) for item in predictions)
    return tuple(sorted(key for key, count in counts.items() if count > 1))


def _non_finite(provider: str, predictions: tuple[ForecastPrediction, ...]) -> tuple[NonFinitePrediction, ...]:
    fields = ("p25", "p50", "p75", "interval_lower", "interval_upper", "baseline_p50")
    return tuple(
        NonFinitePrediction(provider, item.product_id, item.target_date, item.horizon,
                            tuple(field for field in fields if not math.isfinite(getattr(item, field))))
        for item in sorted(predictions, key=_key)
        if any(not math.isfinite(getattr(item, field)) for field in fields)
    )


def compare_forecasts(
    production: ForecastPredictionResult,
    shadow: ForecastPredictionResult,
) -> ForecastCompatibilityReport:
    """Compare normalized predictions without treating numeric drift as failure."""
    production_by_key = {_key(item): item for item in production.predictions}
    shadow_by_key = {_key(item): item for item in shadow.predictions}
    production_keys, shadow_keys = set(production_by_key), set(shadow_by_key)
    missing = tuple(sorted(production_keys - shadow_keys))
    extra = tuple(sorted(shadow_keys - production_keys))
    differences = []
    for key in sorted(production_keys & shadow_keys):
        left, right = production_by_key[key], shadow_by_key[key]
        differences.append(PredictionDifference(
            product_id=key[0], target_date=key[1], horizon=key[2],
            abs_diff_p25=abs(right.p25 - left.p25),
            abs_diff_p50=abs(right.p50 - left.p50),
            abs_diff_p75=abs(right.p75 - left.p75),
            relative_diff_p25=_relative_difference(left.p25, right.p25),
            relative_diff_p50=_relative_difference(left.p50, right.p50),
            relative_diff_p75=_relative_difference(left.p75, right.p75),
        ))
    production_violations = _violations("existing", production.predictions)
    shadow_violations = _violations("shelfcash_forecast", shadow.predictions)
    production_duplicates = _duplicates(production.predictions)
    shadow_duplicates = _duplicates(shadow.predictions)
    production_non_finite = _non_finite("existing", production.predictions)
    shadow_non_finite = _non_finite("shelfcash_forecast", shadow.predictions)
    return ForecastCompatibilityReport(
        compatible=(not missing and not extra and not production_violations and not shadow_violations
                    and not production_duplicates and not shadow_duplicates
                    and not production_non_finite and not shadow_non_finite),
        missing_keys=missing,
        extra_keys=extra,
        production_quantile_violations=production_violations,
        shadow_quantile_violations=shadow_violations,
        production_duplicate_keys=production_duplicates,
        shadow_duplicate_keys=shadow_duplicates,
        production_non_finite_predictions=production_non_finite,
        shadow_non_finite_predictions=shadow_non_finite,
        differences=tuple(differences),
        mean_abs_diff_p25=fmean(item.abs_diff_p25 for item in differences) if differences else None,
        mean_abs_diff_p50=fmean(item.abs_diff_p50 for item in differences) if differences else None,
        mean_abs_diff_p75=fmean(item.abs_diff_p75 for item in differences) if differences else None,
    )

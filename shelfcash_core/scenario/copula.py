from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import norm, rankdata

from shelfcash_core.contracts import ForecastPackage
from shelfcash_core.scenario.bootstrap import (
    ResidualVectorBootstrapScenarioGenerator,
)
from shelfcash_core.scenario.contracts import (
    ProductDemandScenario,
    ProductDemandScenarioBundle,
    ProductDemandScenarioLine,
)
from shelfcash_core.scenario.residuals import validate_residual_history
from shelfcash_core.scenario.validation import scenario_reproduction_diagnostics


class GaussianCopulaScenarioGenerator:
    """Gaussian-copula challenger over empirical scaled-residual marginals."""

    method = "gaussian_copula"

    def __init__(
        self,
        *,
        minimum_samples: int = 20,
        regularization: float = 0.05,
        fallback: ResidualVectorBootstrapScenarioGenerator | None = None,
    ) -> None:
        self.minimum_samples = minimum_samples
        self.regularization = regularization
        self.fallback = fallback or ResidualVectorBootstrapScenarioGenerator()

    def _fallback_bundle(
        self,
        forecast: ForecastPackage,
        residuals: pd.DataFrame,
        n_scenarios: int,
        seed: int,
        reason: str,
    ) -> ProductDemandScenarioBundle:
        bundle = self.fallback.generate(
            forecast,
            residuals,
            n_scenarios=n_scenarios,
            seed=seed,
        )
        return bundle.model_copy(
            update={
                "warnings": sorted(
                    set(bundle.warnings + ["COPULA_FALLBACK_RESIDUAL_BOOTSTRAP"])
                ),
                "diagnostics": {
                    **bundle.diagnostics,
                    "requested_method": self.method,
                    "copula_fallback_reason": reason,
                },
            }
        )

    def generate(
        self,
        forecast: ForecastPackage,
        residual_history: pd.DataFrame,
        *,
        n_scenarios: int,
        seed: int,
    ) -> ProductDemandScenarioBundle:
        if n_scenarios < 1:
            raise ValueError("n_scenarios phải >= 1.")
        residuals = validate_residual_history(residual_history)
        predictions = sorted(
            forecast.predictions,
            key=lambda row: (row.store_id, row.product_id, row.target_date, row.horizon),
        )
        dimensions = [
            (prediction.store_id, prediction.product_id, prediction.horizon)
            for prediction in predictions
        ]
        if len(dimensions) != len(set(dimensions)):
            return self._fallback_bundle(
                forecast, residuals, n_scenarios, seed, "DUPLICATE_DIMENSIONS"
            )

        column_series: dict[tuple[str, str, int], pd.Series] = {}
        for dimension in dimensions:
            store_id, product_id, horizon = dimension
            values = residuals.loc[
                residuals["store_id"].eq(store_id)
                & residuals["product_id"].eq(product_id)
                & residuals["horizon"].eq(horizon),
                ["forecast_origin", "scaled_residual"],
            ].drop_duplicates("forecast_origin")
            if len(values) < self.minimum_samples:
                return self._fallback_bundle(
                    forecast,
                    residuals,
                    n_scenarios,
                    seed,
                    f"INSUFFICIENT_MARGINAL:{dimension!r}",
                )
            column_series[dimension] = values.set_index("forecast_origin")[
                "scaled_residual"
            ]

        matrix = pd.DataFrame(column_series).sort_index().dropna()
        if len(matrix) < self.minimum_samples:
            return self._fallback_bundle(
                forecast, residuals, n_scenarios, seed, "INSUFFICIENT_SHARED_ORIGINS"
            )
        gaussian = np.column_stack(
            [
                norm.ppf(
                    np.clip(
                        (rankdata(matrix[column].to_numpy()) - 0.5) / len(matrix),
                        1e-6,
                        1 - 1e-6,
                    )
                )
                for column in matrix.columns
            ]
        )
        covariance = np.corrcoef(gaussian, rowvar=False)
        if covariance.ndim == 0:
            covariance = np.array([[1.0]])
        covariance = (1 - self.regularization) * covariance + self.regularization * np.eye(
            len(dimensions)
        )
        eigenvalues, eigenvectors = np.linalg.eigh(covariance)
        covariance = eigenvectors @ np.diag(np.clip(eigenvalues, 1e-8, None)) @ eigenvectors.T

        rng = np.random.default_rng(seed)
        gaussian_samples = rng.multivariate_normal(
            np.zeros(len(dimensions)), covariance, size=n_scenarios
        )
        uniforms = norm.cdf(gaussian_samples)
        empirical_samples = np.column_stack(
            [
                np.quantile(
                    matrix[dimension].to_numpy(dtype=float),
                    uniforms[:, index],
                    method="linear",
                )
                for index, dimension in enumerate(dimensions)
            ]
        )

        scenarios: list[ProductDemandScenario] = []
        sampled_records: list[dict[str, Any]] = []
        clipped_count = 0
        for scenario_index in range(n_scenarios):
            scenario_id = f"scenario_{scenario_index + 1:04d}"
            lines: list[ProductDemandScenarioLine] = []
            for dimension_index, prediction in enumerate(predictions):
                scaled_residual = float(empirical_samples[scenario_index, dimension_index])
                spread = max(prediction.p75 - prediction.p25, 1e-6)
                raw_quantity = prediction.p50 + scaled_residual * spread
                clipped_count += int(raw_quantity < 0)
                sampled_records.append(
                    {
                        "scenario_id": scenario_id,
                        "store_id": prediction.store_id,
                        "product_id": prediction.product_id,
                        "horizon": prediction.horizon,
                        "scaled_residual": scaled_residual,
                    }
                )
                lines.append(
                    ProductDemandScenarioLine(
                        scenario_id=scenario_id,
                        store_id=prediction.store_id,
                        product_id=prediction.product_id,
                        product_name=prediction.product_name,
                        product_unit=prediction.unit,
                        target_date=prediction.target_date,
                        horizon=prediction.horizon,
                        demand_quantity=max(0.0, raw_quantity),
                        source_model_version=forecast.model_version,
                        scenario_method=self.method,
                    )
                )
            scenarios.append(
                ProductDemandScenario(
                    scenario_id=scenario_id,
                    probability_weight=1.0 / n_scenarios,
                    lines=lines,
                    metadata={"seed": seed, "copula_regularization": self.regularization},
                )
            )

        diagnostics = scenario_reproduction_diagnostics(
            residuals,
            pd.DataFrame(sampled_records),
            clipped_count=clipped_count,
            total_count=len(sampled_records),
        )
        diagnostics.update(
            {
                "seed": seed,
                "scenario_count": n_scenarios,
                "residual_row_count": len(residuals),
                "minimum_samples": self.minimum_samples,
                "regularization": self.regularization,
            }
        )
        return ProductDemandScenarioBundle(
            forecast_date=forecast.forecast_date,
            horizon=forecast.forecast_horizon,
            model_version=forecast.model_version,
            scenario_method=self.method,
            scenarios=scenarios,
            diagnostics=diagnostics,
        )

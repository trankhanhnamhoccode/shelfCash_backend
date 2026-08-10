from __future__ import annotations

from collections import Counter
from typing import Any

import numpy as np
import pandas as pd

from shelfcash_core.contracts import ForecastPackage, ForecastPrediction
from shelfcash_core.exceptions import ScenarioDataInsufficiencyError
from shelfcash_core.scenario.contracts import (
    ProductDemandScenario,
    ProductDemandScenarioBundle,
    ProductDemandScenarioLine,
)
from shelfcash_core.scenario.residuals import validate_residual_history
from shelfcash_core.scenario.validation import scenario_reproduction_diagnostics


class ResidualVectorBootstrapScenarioGenerator:
    """Sample coherent store/origin residual blocks with explicit fallbacks."""

    method = "residual_bootstrap"

    def __init__(
        self,
        *,
        minimum_block_observations: int = 2,
        minimum_pool_observations: int = 3,
    ) -> None:
        self.minimum_block_observations = minimum_block_observations
        self.minimum_pool_observations = minimum_pool_observations

    def _fallback_pool(
        self,
        residuals: pd.DataFrame,
        prediction: ForecastPrediction,
    ) -> tuple[pd.DataFrame, str]:
        hierarchy = (
            (
                residuals["store_id"].eq(prediction.store_id)
                & residuals["product_id"].eq(prediction.product_id)
                & residuals["horizon"].eq(prediction.horizon),
                "product_store_horizon",
            ),
            (
                residuals["product_id"].eq(prediction.product_id)
                & residuals["horizon"].eq(prediction.horizon),
                "product_global_horizon",
            ),
            (
                residuals["store_id"].eq(prediction.store_id)
                & residuals["horizon"].eq(prediction.horizon),
                "store_global_horizon",
            ),
            (residuals["horizon"].eq(prediction.horizon), "global_horizon"),
            (pd.Series(True, index=residuals.index), "global"),
        )
        for mask, source in hierarchy:
            pool = residuals.loc[mask]
            if len(pool) >= self.minimum_pool_observations:
                return pool, source
        raise ScenarioDataInsufficiencyError(
            "Không đủ residual history cho prediction và các fallback pools.",
            details={
                "store_id": prediction.store_id,
                "product_id": prediction.product_id,
                "horizon": prediction.horizon,
                "minimum_pool_observations": self.minimum_pool_observations,
            },
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
        prediction_keys = [
            (row.store_id, row.product_id, row.target_date) for row in predictions
        ]
        if len(prediction_keys) != len(set(prediction_keys)):
            raise ValueError("SCENARIO_DUPLICATE_KEY trong ForecastPackage.")

        blocks = {
            (str(store_id), pd.Timestamp(origin)): group.copy()
            for (store_id, origin), group in residuals.groupby(
                ["store_id", "forecast_origin"], observed=True
            )
            if len(group) >= self.minimum_block_observations
        }
        if not blocks:
            raise ScenarioDataInsufficiencyError(
                "Không có residual block đủ observations.",
                details={
                    "minimum_block_observations": self.minimum_block_observations
                },
            )

        stores = sorted({prediction.store_id for prediction in predictions})
        rng = np.random.default_rng(seed)
        fallback_counts: Counter[str] = Counter()
        sampled_records: list[dict[str, Any]] = []
        scenarios: list[ProductDemandScenario] = []
        clipped_count = 0
        total_count = 0

        for scenario_index in range(n_scenarios):
            scenario_id = f"scenario_{scenario_index + 1:04d}"
            selected_blocks: dict[str, tuple[str, pd.Timestamp]] = {}
            for store_id in stores:
                store_blocks = [key for key in blocks if key[0] == store_id]
                candidates = store_blocks or list(blocks)
                selected_blocks[store_id] = candidates[int(rng.integers(len(candidates)))]

            lines: list[ProductDemandScenarioLine] = []
            for prediction in predictions:
                block_key = selected_blocks[prediction.store_id]
                block = blocks[block_key]
                exact = block.loc[
                    block["store_id"].eq(prediction.store_id)
                    & block["product_id"].eq(prediction.product_id)
                    & block["horizon"].eq(prediction.horizon)
                ]
                if exact.empty:
                    pool, fallback_source = self._fallback_pool(residuals, prediction)
                    sampled_row = pool.iloc[int(rng.integers(len(pool)))]
                    fallback_counts[fallback_source] += 1
                else:
                    sampled_row = exact.iloc[0]
                    fallback_counts["coherent_block"] += 1

                scaled_residual = float(sampled_row["scaled_residual"])
                spread = max(prediction.p75 - prediction.p25, 1e-6)
                raw_quantity = prediction.p50 + scaled_residual * spread
                quantity = max(0.0, raw_quantity)
                clipped_count += int(raw_quantity < 0)
                total_count += 1
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
                        demand_quantity=quantity,
                        source_model_version=forecast.model_version,
                        scenario_method=self.method,
                    )
                )

            scenarios.append(
                ProductDemandScenario(
                    scenario_id=scenario_id,
                    probability_weight=1.0 / n_scenarios,
                    lines=lines,
                    metadata={
                        "seed": seed,
                        "sampled_blocks": {
                            store_id: {
                                "source_store_id": key[0],
                                "forecast_origin": key[1].date().isoformat(),
                            }
                            for store_id, key in selected_blocks.items()
                        },
                    },
                )
            )

        diagnostics = scenario_reproduction_diagnostics(
            residuals,
            pd.DataFrame(sampled_records),
            clipped_count=clipped_count,
            total_count=total_count,
        )
        diagnostics.update(
            {
                "seed": seed,
                "scenario_count": n_scenarios,
                "residual_row_count": len(residuals),
                "fallback_counts": dict(sorted(fallback_counts.items())),
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

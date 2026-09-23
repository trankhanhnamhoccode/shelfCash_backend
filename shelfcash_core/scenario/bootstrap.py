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


MAX_UNIQUE_COHERENT_BLOCK_POOL = 100


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

    @staticmethod
    def _ordered_block_keys(
        blocks: dict[tuple[str, pd.Timestamp], pd.DataFrame],
        store_id: str,
    ) -> list[tuple[str, pd.Timestamp]]:
        """Return the stable candidate support for one forecast store.

        The global fallback is retained for the pre-existing multi-store core
        behavior.  Decision Runs currently construct one-store packages, so
        ordinary stochastic planning has one coherent empirical support.
        """
        store_blocks = [key for key in blocks if key[0] == store_id]
        return sorted(store_blocks or list(blocks), key=lambda key: (key[0], key[1]))

    def _select_coherent_blocks(
        self,
        blocks: dict[tuple[str, pd.Timestamp], pd.DataFrame],
        stores: list[str],
        *,
        n_scenarios: int,
        rng: np.random.Generator,
    ) -> tuple[list[dict[str, tuple[str, pd.Timestamp]]], dict[str, Any]]:
        """Select one coherent origin per store for each generated scenario.

        Small empirical supports are sampled without replacement.  Repeating
        the same small set cannot create independent evidence, so when the
        caller asks for at least the support size we enumerate it exactly once.
        Larger pools deliberately retain the established bootstrap-with-
        replacement behavior.
        """
        candidates_by_store = {
            store_id: self._ordered_block_keys(blocks, store_id)
            for store_id in stores
        }
        support_sizes = {
            store_id: len(candidates)
            for store_id, candidates in candidates_by_store.items()
        }
        # ``blocks`` is non-empty, and the global fallback above guarantees a
        # non-empty candidate set for every forecast store.
        smallest_support = min(support_sizes.values())
        small_pool = all(
            count <= MAX_UNIQUE_COHERENT_BLOCK_POOL
            for count in support_sizes.values()
        )

        if small_pool:
            generated_count = min(n_scenarios, smallest_support)
            sampling_mode = (
                "enumerate_all"
                if n_scenarios >= smallest_support
                else "without_replacement"
            )
            schedules: dict[str, list[tuple[str, pd.Timestamp]]] = {}
            for store_id, candidates in candidates_by_store.items():
                if generated_count >= len(candidates):
                    schedules[store_id] = candidates
                else:
                    selected_indexes = rng.choice(
                        len(candidates), size=generated_count, replace=False
                    )
                    schedules[store_id] = [
                        candidates[int(index)] for index in selected_indexes
                    ]
        else:
            generated_count = n_scenarios
            sampling_mode = "bootstrap_with_replacement"
            schedules = {
                store_id: [
                    candidates[int(rng.integers(len(candidates)))]
                    for _ in range(generated_count)
                ]
                for store_id, candidates in candidates_by_store.items()
            }

        selections = [
            {
                store_id: schedules[store_id][scenario_index]
                for store_id in stores
            }
            for scenario_index in range(generated_count)
        ]
        distinct_selected = {
            store_id: len(set(schedules[store_id]))
            for store_id in stores
        }
        diagnostics: dict[str, Any] = {
            # Scalar values describe the limiting support when a core caller
            # supplies more than one store.  The per-store map retains the
            # exact provenance without changing ordinary single-store output.
            "eligible_coherent_block_count": smallest_support,
            "selected_coherent_block_count": generated_count,
            "distinct_selected_coherent_block_count": min(
                distinct_selected.values()
            ),
            "coherent_block_sampling_mode": sampling_mode,
        }
        if len(stores) > 1:
            diagnostics["eligible_coherent_block_counts_by_store"] = support_sizes
            diagnostics["distinct_selected_coherent_block_counts_by_store"] = (
                distinct_selected
            )
        return selections, diagnostics

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
        coherent_block_selections, block_diagnostics = self._select_coherent_blocks(
            blocks, stores, n_scenarios=n_scenarios, rng=rng
        )
        fallback_counts: Counter[str] = Counter()
        sampled_records: list[dict[str, Any]] = []
        scenarios: list[ProductDemandScenario] = []
        clipped_count = 0
        total_count = 0

        generated_count = len(coherent_block_selections)
        for scenario_index, selected_blocks in enumerate(coherent_block_selections):
            scenario_id = f"scenario_{scenario_index + 1:04d}"

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
                    probability_weight=1.0 / generated_count,
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
                # Retained for existing bundle-diagnostic readers; it is the
                # count actually materialized in ``scenarios``.
                "scenario_count": generated_count,
                "scenario_count_requested": n_scenarios,
                "scenario_count_generated": generated_count,
                "residual_row_count": len(residuals),
                "fallback_counts": dict(sorted(fallback_counts.items())),
                **block_diagnostics,
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

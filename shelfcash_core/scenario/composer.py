from __future__ import annotations

from typing import Protocol

import pandas as pd

from shelfcash_core.contracts import ForecastPackage
from shelfcash_core.scenario.bootstrap import (
    ResidualVectorBootstrapScenarioGenerator,
)
from shelfcash_core.scenario.contracts import ProductDemandScenarioBundle
from shelfcash_core.scenario.copula import GaussianCopulaScenarioGenerator


class ScenarioGenerator(Protocol):
    method: str

    def generate(
        self,
        forecast: ForecastPackage,
        residual_history: pd.DataFrame,
        *,
        n_scenarios: int,
        seed: int,
    ) -> ProductDemandScenarioBundle: ...


def generate_product_demand_scenarios(
    forecast_package: ForecastPackage,
    residual_history: pd.DataFrame,
    *,
    n_scenarios: int,
    seed: int,
    method: str = "residual_bootstrap",
) -> ProductDemandScenarioBundle:
    generators: dict[str, ScenarioGenerator] = {
        "residual_bootstrap": ResidualVectorBootstrapScenarioGenerator(),
        "gaussian_copula": GaussianCopulaScenarioGenerator(),
    }
    try:
        generator = generators[method]
    except KeyError as exc:
        raise ValueError(
            f"scenario method không được hỗ trợ: {method!r}; "
            f"chọn một trong {sorted(generators)}."
        ) from exc
    return generator.generate(
        forecast_package,
        residual_history,
        n_scenarios=n_scenarios,
        seed=seed,
    )

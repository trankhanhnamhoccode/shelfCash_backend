from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date

from shelfcash_core.optimization.contracts import (
    OptimizationRequest,
    OptimizationResult,
    RollingHorizonResult,
    RollingHorizonStep,
)
from shelfcash_core.optimization.optimizer import optimize_procurement


class RollingHorizonController:
    """Pure MPC orchestration; state observation/reforecasting stay caller-owned."""

    def run(
        self,
        decision_dates: Sequence[date],
        request_factory: Callable[[date, OptimizationResult | None], OptimizationRequest],
        *,
        optimizer: Callable[[OptimizationRequest], OptimizationResult] = optimize_procurement,
    ) -> RollingHorizonResult:
        steps: list[RollingHorizonStep] = []
        previous: OptimizationResult | None = None
        for decision_date in decision_dates:
            request = request_factory(decision_date, previous)
            if request.decision_date != decision_date:
                raise ValueError("request_factory returned the wrong decision_date.")
            result = optimizer(request)
            evaluation = (
                result.evaluations.get(result.recommended_strategy)
                if result.recommended_strategy is not None
                else None
            )
            executed = (
                [
                    order
                    for order in evaluation.plan.orders
                    if order.order_date == decision_date
                ]
                if evaluation is not None
                else []
            )
            steps.append(
                RollingHorizonStep(
                    decision_date=decision_date,
                    optimization_result=result,
                    executed_orders=executed,
                )
            )
            previous = result
        return RollingHorizonResult(
            steps=steps,
            provenance={
                "controller": "receding_horizon_v1",
                "execution_rule": "first_actionable_period_only",
            },
        )

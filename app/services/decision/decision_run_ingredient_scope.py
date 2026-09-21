"""Persisted Decision Package ingredient membership for Explanation."""

from typing import Any


def decision_run_ingredient_ids(package: dict[str, Any]) -> set[str]:
    """Return the authoritative ingredient IDs from one persisted package.

    Explanation membership is deliberately narrower than all derived package
    evidence: only persisted demand and selected-plan items establish a target.
    """
    ingredient_ids: set[str] = set()
    recommended_plan = package.get("recommended_plan")
    sources = (
        package.get("ingredient_demand", []),
        recommended_plan.get("items", []) if isinstance(recommended_plan, dict) else [],
    )
    for rows in sources:
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict) and isinstance(row.get("ingredient_id"), str):
                ingredient_id = row["ingredient_id"].strip()
                if ingredient_id:
                    ingredient_ids.add(ingredient_id)
    return ingredient_ids

"""Focused read-only application boundary for persisted Decision Packages."""

import json

from app.core.exceptions import PlanningError
from app.models.decision import DecisionRunModel


class ReadDecisionPackage:
    """Read one persisted Decision Package without changing runtime state."""

    def __init__(self, session_factory):
        self._session_factory = session_factory

    def read(self, decision_run_id: str) -> dict:
        with self._session_factory() as session:
            run = session.get(DecisionRunModel, decision_run_id)
            if not run:
                raise PlanningError(
                    "DECISION_RUN_NOT_FOUND",
                    "Decision run not found.",
                    {"decision_run_id": decision_run_id},
                    http_status=404,
                )
            return json.loads(run.package_json)

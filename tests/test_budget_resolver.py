from datetime import date
from decimal import Decimal

import pytest

from app.core.exceptions import PlanningError
from app.models.business import InventoryConstraintModel, StoreSettingsModel
from app.models.operations import BudgetPeriodModel
from app.services.budget_resolver import BudgetResolver


def budget_constraint(identifier="business-budget",value="700",unit="VND"):
    return InventoryConstraintModel(constraint_id=identifier,store_id="STORE_001",ingredient_id=None,
        constraint_type="budget",value=Decimal(value),unit=unit,effective_date=date(2026,7,1),
        version=1,active=True,source="test")


def test_budget_override_zero_is_configured_and_null_uses_next_source(session_factory):
    with session_factory() as session:
        resolver=BudgetResolver(session)
        zero=resolver.resolve("STORE_001",date(2026,8,4),0,date(2026,8,10))
        assert zero.limit == 0 and zero.trace["source"] == "request_override"
        assert zero.trace["period_start"] == "2026-08-04" and zero.trace["period_end"] == "2026-08-10"
        assert resolver.resolve("STORE_001",date(2026,8,4),None).trace["source"] == "not_configured"


def test_budget_precedence_period_business_constraint_and_legacy(session_factory):
    with session_factory() as session:
        session.add(StoreSettingsModel(setting_id="legacy-budget",store_id="STORE_001",monthly_budget=900,
            forecast_horizon=7,default_strategy="balanced",version=1))
        session.add(budget_constraint())
        session.add(BudgetPeriodModel(budget_period_id="aug-budget",store_id="STORE_001",period="2026-08",
            monthly_budget=600,reserved_budget=100,spent_budget=50));session.flush()
        period=BudgetResolver(session).resolve("STORE_001",date(2026,8,4))
        assert period.limit == 450 and period.trace["source"] == "budget_period"
        assert period.trace["period_policy"] == "REMAINING_AS_OF_MONTH_CAP"
        session.delete(session.get(BudgetPeriodModel,"aug-budget"));session.flush()
        business=BudgetResolver(session).resolve("STORE_001",date(2026,8,4))
        assert business.limit == 700 and business.trace["source"] == "business_constraint"
        session.delete(session.get(InventoryConstraintModel,"business-budget"));session.flush()
        legacy=BudgetResolver(session).resolve("STORE_001",date(2026,8,4))
        assert legacy.limit == 900 and legacy.trace["source"] == "legacy_store_settings"


def test_budget_period_zero_and_currency_mismatch(session_factory):
    with session_factory() as session:
        session.add(BudgetPeriodModel(budget_period_id="zero-budget",store_id="STORE_001",period="2026-08",
            monthly_budget=0,reserved_budget=0,spent_budget=0));session.flush()
        resolved=BudgetResolver(session).resolve("STORE_001",date(2026,8,4))
        assert resolved.limit == 0 and resolved.trace["configured"] is True
        session.delete(session.get(BudgetPeriodModel,"zero-budget"));session.add(budget_constraint(unit="USD"));session.flush()
        with pytest.raises(PlanningError) as exc:
            BudgetResolver(session).resolve("STORE_001",date(2026,8,4))
        assert exc.value.code == "BUSINESS_CONSTRAINT_UNIT_INVALID"

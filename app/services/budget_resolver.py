import logging
from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.core.exceptions import PlanningError
from app.models.business import StoreSettingsModel
from app.models.operations import BudgetPeriodModel
from app.services.business_constraint_resolver import BusinessConstraintResolver

logger=logging.getLogger(__name__)


@dataclass(frozen=True)
class ResolvedBudget:
    limit:int|None
    trace:dict


class BudgetResolver:
    """Select exactly one VND planning cap; budget sources are never combined."""
    def __init__(self,session):
        self.session=session;self.constraints=BusinessConstraintResolver(session)

    def resolve(self,store_id,as_of_date,requested_override=None,horizon_end=None):
        if requested_override is not None:
            return self._result(int(requested_override),"request_override",as_of_date,horizon_end or as_of_date)
        period=as_of_date.strftime("%Y-%m")
        budget_period=self.session.scalar(select(BudgetPeriodModel).where(
            BudgetPeriodModel.store_id==store_id,BudgetPeriodModel.period==period))
        period_start=date(as_of_date.year,as_of_date.month,1)
        period_end=date(as_of_date.year,as_of_date.month,monthrange(as_of_date.year,as_of_date.month)[1])
        if budget_period is not None:
            remaining=max(0,budget_period.monthly_budget-budget_period.reserved_budget-budget_period.spent_budget)
            return self._result(remaining,"budget_period",period_start,period_end)
        constraint=self.constraints.resolve_constraint(store_id,"budget",None,as_of_date)
        if constraint is not None:
            constraint_currency=constraint.currency or constraint.unit
            if str(constraint_currency or "").upper()!="VND":
                raise PlanningError("BUSINESS_CONSTRAINT_UNIT_INVALID","Budget constraint currency is not supported.",{
                    "constraint_id":constraint.constraint_id,"constraint_type":"budget","currency":constraint_currency,
                    "allowed_currencies":["VND"]})
            value=Decimal(constraint.value)
            if value!=value.to_integral_value():
                raise PlanningError("BUSINESS_CONSTRAINT_VALUE_INVALID","VND budget must be an integer amount.",{
                    "constraint_id":constraint.constraint_id,"value":str(value),"currency":"VND"})
            return self._result(int(value),"business_constraint",constraint.effective_date,constraint.end_date)
        settings=self.session.scalar(select(StoreSettingsModel).where(StoreSettingsModel.store_id==store_id))
        if settings is not None and settings.monthly_budget>0:
            logger.warning("legacy_store_settings_budget_used store_id=%s",store_id)
            return self._result(settings.monthly_budget,"legacy_store_settings",period_start,period_end)
        return ResolvedBudget(None,{"configured":False,"source":"not_configured","value":None,"currency":"VND",
            "period_start":None,"period_end":None,"period_policy":"NONE"})

    @staticmethod
    def _result(value,source,period_start,period_end):
        policy="REQUEST_SCOPED_CAP" if source=="request_override" else (
            "REMAINING_AS_OF_MONTH_CAP" if source in {"budget_period","legacy_store_settings"} else "EFFECTIVE_CONSTRAINT_CAP")
        return ResolvedBudget(value,{"configured":True,"source":source,"value":value,"currency":"VND",
            "period_start":period_start.isoformat() if period_start else None,
            "period_end":period_end.isoformat() if period_end else None,"period_policy":policy})

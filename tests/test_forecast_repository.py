from datetime import date
from decimal import Decimal
from uuid import uuid4

import pandas as pd
import pytest

from app.core.exceptions import InsufficientTrainingDataError
from app.models.business import CalendarFeatureModel, ProductModel, SalesDailyModel
from app.repositories.forecast_data import ForecastDataRepository


def test_sales_mapping_preserves_product_identity_and_nullable_stockout(session_factory):
    with session_factory() as session:
        for product_id in ("p-a", "p-b"):
            session.add(ProductModel(product_id=product_id, store_id="STORE_001", product="Same name",
                normalized_name="same name", selling_unit="cái", active=True, source="test"))
            session.add(SalesDailyModel(sales_record_id=str(uuid4()), store_id="STORE_001", date=date(2026, 8, 1),
                product_id=product_id, quantity=Decimal("2.5"), unit_price=10, promotion=False,
                is_stockout=None, source="test"))
        session.commit()
        frame = ForecastDataRepository(session).sales_history("STORE_001", date(2026,8,1), date(2026,8,1))
    assert frame["product_id"].tolist() == ["p-a", "p-b"]
    assert frame["quantity_sold"].dtype.kind == "f"
    assert frame["is_stockout"].isna().all()
    assert frame["revenue"].tolist() == [25.0, 25.0]


def test_calendar_includes_future_horizon_and_empty_sales_is_domain_error(session_factory):
    with session_factory() as session:
        session.add(CalendarFeatureModel(calendar_feature_id=str(uuid4()), store_id="STORE_001",
            date=date(2026,8,10), is_weekend=False, is_holiday=False, is_store_closed=False,
            is_promotion=True, promotion_name="future", source="test")); session.commit()
        repo=ForecastDataRepository(session)
        frame=repo.calendar_features("STORE_001", date(2026,8,1), date(2026,8,10))
        assert frame.iloc[0]["promotion_name"] == "future"
        with pytest.raises(InsufficientTrainingDataError):
            repo.sales_history("STORE_001", date(2020,1,1), date(2020,1,2))

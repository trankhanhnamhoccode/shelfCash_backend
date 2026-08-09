from datetime import date

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import InsufficientTrainingDataError
from app.models.business import CalendarFeatureModel, ProductModel, SalesDailyModel


class ForecastDataRepository:
    def __init__(self, session: Session):
        self.session = session

    def sales_history(self, store_id: str, start: date, end: date) -> pd.DataFrame:
        rows = self.session.execute(
            select(SalesDailyModel, ProductModel)
            .join(ProductModel, ProductModel.product_id == SalesDailyModel.product_id)
            .where(SalesDailyModel.store_id == store_id, ProductModel.store_id == store_id,
                   SalesDailyModel.date >= start, SalesDailyModel.date <= end)
            .order_by(SalesDailyModel.date, SalesDailyModel.product_id, SalesDailyModel.sales_record_id)
        ).all()
        if not rows:
            raise InsufficientTrainingDataError(details={"store_id": store_id, "history_start": start, "history_end": end, "available_target_dates": 0})
        data = [{
            "date": sale.date, "store_id": sale.store_id, "product_id": sale.product_id,
            "product_name": product.product, "quantity_sold": float(sale.quantity),
            "unit": product.selling_unit, "selling_price": sale.unit_price,
            "revenue": float(sale.quantity) * sale.unit_price if sale.unit_price is not None else None,
            "is_stockout": sale.is_stockout,
            "promotion_name": "promotion" if sale.promotion else None,
        } for sale, product in rows]
        frame = pd.DataFrame(data)
        frame["quantity_sold"] = pd.to_numeric(frame["quantity_sold"])
        frame["is_stockout"] = frame["is_stockout"].astype("boolean")
        return frame.sort_values(["date", "product_id"], kind="stable").reset_index(drop=True)

    def calendar_features(self, store_id: str, start: date, end: date) -> pd.DataFrame:
        rows = self.session.scalars(
            select(CalendarFeatureModel).where(CalendarFeatureModel.store_id == store_id,
                CalendarFeatureModel.date >= start, CalendarFeatureModel.date <= end)
            .order_by(CalendarFeatureModel.date)
        ).all()
        columns = ["date", "is_weekend", "is_holiday", "is_store_closed", "is_promotion",
                   "promotion_name", "temperature", "rainfall", "known_at"]
        return pd.DataFrame([{
            "date": row.date, "is_weekend": row.is_weekend, "is_holiday": row.is_holiday,
            "is_store_closed": row.is_store_closed, "is_promotion": row.is_promotion,
            "promotion_name": row.promotion_name,
            "temperature": float(row.temperature) if row.temperature is not None else None,
            "rainfall": float(row.rainfall) if row.rainfall is not None else None,
            "known_at": row.created_at,
        } for row in rows], columns=columns)

from io import BytesIO

import pandas as pd

from app.core.names import normalize_name
from app.core.units import normalize_unit
from app.models.business import IngredientModel


def business_rules_workbook():
    frame = pd.DataFrame([
        {"Constraint Type": "safety_stock", "Ingredient": "Excel milk", "Value": 12, "Unit": "liter", "Effective Date": "2026-07-01"},
        {"Constraint Type": "maximum_stock", "Ingredient": "Excel milk", "Value": 40, "Unit": "liter", "Effective Date": "2026-07-01"},
        {"Constraint Type": "shelf_life_target", "Ingredient": "Excel milk", "Value": 7, "Unit": "day", "Effective Date": "2026-07-01"},
        {"Constraint Type": "service_level_target", "Ingredient": None, "Value": 95, "Unit": "percent", "Effective Date": "2026-07-01"},
        {"Constraint Type": "storage_capacity", "Ingredient": None, "Value": 1000, "Unit": "liter", "Effective Date": "2026-07-01"},
    ])
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        frame.to_excel(writer, sheet_name="Business Rules", index=False)
    return output.getvalue()


def test_excel_business_rules_processes_duration_ratio_quantity_and_capacity(client):
    with client.app.state.session_factory() as session:
        session.add(IngredientModel(ingredient_id="excel-milk", store_id="STORE_001", ingredient="Excel milk",
            normalized_name=normalize_name("Excel milk"), base_unit=normalize_unit("liter"), active=True, source="test")); session.commit()
    created = client.post("/api/v1/imports", data={"store_id": "STORE_001", "forecast_date": "2026-08-03"},
        files={"files": ("04_Recipes_BusinessRules_fixed.xlsx", business_rules_workbook(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert created.status_code == 201, created.text
    body = created.json(); sheet = body["sheets"][0]
    mapping = {"Constraint Type": "constraint_type", "Ingredient": "ingredient_name", "Value": "value",
        "Unit": "unit", "Effective Date": "effective_date"}
    confirmed = client.post(f"/api/v1/imports/{body['import_id']}/confirm", json={"mappings": [{"sheet_id": sheet["sheet_id"],
        "sheet_type": "business_constraints", "column_mapping": mapping}]})
    assert confirmed.status_code == 200, confirmed.text
    processed = client.post(f"/api/v1/imports/{body['import_id']}/process")
    assert processed.status_code == 200, processed.text
    response = client.get("/api/v1/stores/STORE_001/inventory-constraints")
    assert response.status_code == 200
    items = {item["constraint_type"]: item for item in response.json()["items"]}
    assert items["shelf_life_target"]["value"] == "7.000000" and items["shelf_life_target"]["unit"] == "day"
    assert items["service_level_target"]["value"] == "0.950000" and items["service_level_target"]["unit"] == "ratio"

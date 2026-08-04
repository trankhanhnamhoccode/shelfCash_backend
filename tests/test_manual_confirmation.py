from app.core.canonical_schemas import CANONICAL_SCHEMAS
from app.core.names import normalize_name
from app.models.business import IngredientModel
from tests.test_excel_upload import upload_fake


def test_manual_confirmation_and_process(client):
    with client.app.state.session_factory() as session:
        session.add(IngredientModel(ingredient_id="manual-milk", store_id="STORE_001", ingredient="Sữa",
            normalized_name=normalize_name("Sữa"), base_unit="lít", active=True, source="test"))
        session.commit()
    uploaded = upload_fake(client)
    assert uploaded.status_code == 201, uploaded.text
    body = uploaded.json()
    target = next(sheet for sheet in body["sheets"] if sheet["profile"]["sheet_name"] == "Điều kiện vận hành")
    confirmation = {
        "mappings": [{
            "sheet_id": target["sheet_id"], "sheet_type": "business_constraints",
            "column_mapping": {
                "Loại điều kiện": "constraint_type", "Áp dụng cho NL": "ingredient_name",
                "Giá trị": "value", "Bắt đầu": "effective_date", "Ghi chú": "note",
            },
        }]
    }
    confirmed = client.post(f"/api/v1/imports/{body['import_id']}/confirm", json=confirmation)
    assert confirmed.status_code == 200, confirmed.text
    mapping = next(s for s in confirmed.json()["mappings"] if s["sheet_id"] == target["sheet_id"])["mapping"]
    assert mapping["sheet_type"] == "business_constraints"
    assert not mapping["errors"]

    processed = client.post(f"/api/v1/imports/{body['import_id']}/process")
    assert processed.status_code == 200, processed.text
    result = client.get(f"/api/v1/imports/{body['import_id']}/result")
    assert result.status_code == 200
    canonical = result.json()
    assert canonical["store_id"] == "STORE_001"
    assert canonical["business_constraints"][0]["constraint_type"] == "maximum stock"
    assert canonical["business_constraints"][0]["effective_date"] == "2026-07-01"
    for sheet_type, records in canonical.items():
        if sheet_type not in CANONICAL_SCHEMAS:
            continue
        allowed = set(CANONICAL_SCHEMAS[sheet_type]["fields"]) | {"_source_file", "_source_sheet", "_source_excel_row"}
        assert all(set(record) <= allowed for record in records)


def test_changed_sheet_type_revalidates_and_drops_stale_nulls(client):
    body = upload_fake(client).json()
    target = next(sheet for sheet in body["sheets"] if sheet["profile"]["sheet_name"] == "KiemKe_27-07")
    payload = {"mappings": [{"sheet_id": target["sheet_id"], "sheet_type": "business_constraints", "column_mapping": {"Ngày kiểm kê": "effective_date", "Nguyên liệu": "ingredient_name", "Tồn kho": "value", "Đơn vị": "unit"}}]}
    response = client.post(f"/api/v1/imports/{body['import_id']}/confirm", json=payload)
    assert response.status_code == 200, response.text
    mapping = next(s["mapping"] for s in response.json()["mappings"] if s["sheet_id"] == target["sheet_id"])
    assert mapping["column_mapping"]["Tồn kho"] == "value"


def test_reject_field_outside_confirmed_schema(client):
    body = upload_fake(client).json()
    target = body["sheets"][0]
    payload = {"mappings": [{"sheet_id": target["sheet_id"], "sheet_type": "inventory", "column_mapping": {target["profile"]["columns"][0]: "revenue"}}]}
    response = client.post(f"/api/v1/imports/{body['import_id']}/confirm", json=payload)
    assert response.status_code == 422

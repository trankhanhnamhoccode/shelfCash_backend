from io import BytesIO

from tests.fixtures.build_fake_workbook import build_fake_workbook


def upload_fake(client):
    return client.post(
        "/api/v1/imports",
        data={"store_id": "STORE_001", "forecast_date": "2026-07-27", "forecast_horizon": "7"},
        files={"files": ("fake.xlsx", build_fake_workbook(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )


def test_upload_workbook(client):
    response = upload_fake(client)
    assert response.status_code == 201, response.text
    body = response.json()
    assert len(body["sheets"]) == 7
    assert {s["profile"]["sheet_name"] for s in body["sheets"]} >= {"Điều kiện vận hành", "POS_T7_2026"}


def test_invalid_extension(client):
    response = client.post("/api/v1/imports", data={"store_id": "S"}, files={"files": ("bad.txt", b"a,b\n1,2", "text/plain")})
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_file_extension"


def test_file_too_large(client):
    response = client.post("/api/v1/imports", data={"store_id": "S"}, files={"files": ("huge.xlsx", b"x" * (1024 * 1024 + 1), "application/octet-stream")})
    assert response.status_code == 413
    assert response.json()["code"] == "file_too_large"

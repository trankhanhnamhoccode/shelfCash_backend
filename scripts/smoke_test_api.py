import os

import httpx

BASE_URL = os.getenv("API_URL", "http://127.0.0.1:8000")
API_KEY = os.getenv("SHELFCASH_API_KEY", "")
headers = {"X-ShelfCash-Key": API_KEY} if API_KEY else {}

with httpx.Client(base_url=BASE_URL, timeout=30) as client:
    health = client.get("/health")
    health.raise_for_status()
    print("health:", health.json())
    llm = client.get("/api/v1/llm/health")
    llm.raise_for_status()
    print("llm:", llm.json())
    with open("runtime/fake_shelfcash.xlsx", "rb") as workbook:
        response = client.post(
            "/api/v1/imports", headers=headers,
            data={"store_id": "STORE_001", "forecast_date": "2026-07-27", "forecast_horizon": "7"},
            files={"files": ("fake_shelfcash.xlsx", workbook, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
    response.raise_for_status()
    print("import:", response.json()["import_id"], "sheets:", len(response.json()["sheets"]))

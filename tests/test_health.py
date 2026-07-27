def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "shelfcash-backend"}


def test_llm_health_disabled(client):
    response = client.get("/api/v1/llm/health")
    assert response.status_code == 200
    assert response.json()["provider"] == "disabled"

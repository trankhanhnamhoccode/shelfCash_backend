def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "shelfcash-backend",
        "version": "1.0.0",
        "database": "ready",
    }
    assert response.headers["X-Request-ID"]


def test_llm_health_unconfigured(client):
    response = client.get("/api/v1/llm/health")
    assert response.status_code == 200
    data = response.json()
    assert data["provider"] == "openrouter_qwen"
    assert data["model"] == "qwen/qwen3.5-9b"
    assert data["configured"] is False
    assert data["available"] is False

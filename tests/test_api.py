from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app


def test_health_and_local_search() -> None:
    with TestClient(app) as client:
        frontend = client.get("/")
        assert frontend.status_code == 200
        assert "Neethi" in frontend.text
        evaluation = client.get("/evaluation")
        assert evaluation.status_code == 200
        assert "Retrieval evaluation" in evaluation.text

        health = client.get("/health")
        assert health.status_code == 200

        response = client.post(
            "/api/search",
            json={"query": "My neighbour hit me and caused an injury", "limit": 6},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["sources"]
        assert all(source["source_id"] for source in payload["sources"])


def test_chat_without_key_returns_retrieved_citations(monkeypatch) -> None:
    settings = Settings(gemini_api_key=None)
    monkeypatch.setattr("app.main.get_settings", lambda: settings)
    with TestClient(app) as client:
        response = client.post(
            "/api/chat",
            json={"message": "My neighbour hit me and caused an injury"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["grounded"] is True
        assert payload["legal_references"]

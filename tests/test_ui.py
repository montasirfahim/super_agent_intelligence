from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_root_serves_dashboard_html():
    response = client.get("/")
    assert response.status_code == 200
    assert "Super Agent Intelligence" in response.text

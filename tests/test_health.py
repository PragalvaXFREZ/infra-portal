from fastapi.testclient import TestClient

from infra_portal.config import get_settings
from infra_portal.db import get_engine
from infra_portal.main import app


def test_livez_does_not_need_the_database(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://nobody@127.0.0.1:1/none")
    get_settings.cache_clear()
    get_engine.cache_clear()

    response = TestClient(app).get("/livez")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_reports_an_unreachable_database(monkeypatch):
    # Port 1 on localhost refuses connections, so this fails fast.
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://nobody@127.0.0.1:1/none")
    get_settings.cache_clear()
    get_engine.cache_clear()

    response = TestClient(app).get("/readyz")

    assert response.status_code == 503
    assert response.json()["database"] == "unreachable"

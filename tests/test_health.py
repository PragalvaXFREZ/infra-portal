from fastapi.testclient import TestClient

UNREACHABLE = "postgresql+psycopg://nobody@127.0.0.1:1/none"


def _client(monkeypatch):
    from infra_portal import config, db

    # Port 1 on localhost refuses connections, so the check fails fast.
    monkeypatch.setenv("DATABASE_URL", UNREACHABLE)
    config.get_settings.cache_clear()
    db.get_engine.cache_clear()

    from infra_portal.main import create_app

    return TestClient(create_app())


def test_livez_does_not_need_the_database(monkeypatch):
    response = _client(monkeypatch).get("/livez")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_reports_an_unreachable_database(monkeypatch):
    response = _client(monkeypatch).get("/readyz")

    assert response.status_code == 503
    assert response.json()["database"] == "unreachable"


def test_favicon(monkeypatch):
    response = _client(monkeypatch).get("/favicon.ico")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"

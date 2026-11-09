import os

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



def test_static_url_changes_when_the_file_changes(monkeypatch, tmp_path):
    from infra_portal import templating

    monkeypatch.setattr(templating, "STATIC_DIR", tmp_path)
    css = tmp_path / "site.css"
    css.write_text("body { color: red; }")
    first = templating.static_url("site.css")
    css.write_text("body { color: blue; }")
    # Linux keeps file times coarsely; make the change visible, as a new deploy would.
    os.utime(css, ns=(css.stat().st_atime_ns, css.stat().st_mtime_ns + 1_000_000_000))
    second = templating.static_url("site.css")

    assert first.startswith("/static/site.css?v=")
    assert first != second

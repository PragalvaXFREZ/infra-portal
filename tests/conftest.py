"""Shared test setup.

Tests run against in-memory SQLite and a fake GitHub client, so they need no
Docker and no network. The Compose job in CI covers the real Postgres and the
migrations.
"""

from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from infra_portal import config, db
from infra_portal.github import GitHubUser, get_github_client
from infra_portal.models import Base, Member

TEST_ENV = {
    "DATABASE_URL": "sqlite+pysqlite:///:memory:",
    "BASE_URL": "http://testserver",
    "GITHUB_CLIENT_ID": "test-client-id",
    "GITHUB_CLIENT_SECRET": "test-client-secret",
    "SESSION_SECRET": "test-session-secret-that-is-long-enough-000",
    "COOKIE_SECURE": "false",
    "ADMIN_GITHUB_LOGINS": "Boss",
}


@pytest.fixture(autouse=True)
def test_env(monkeypatch):
    for key, value in TEST_ENV.items():
        monkeypatch.setenv(key, value)
    config.get_settings.cache_clear()
    db.get_engine.cache_clear()
    db.get_sessionmaker.cache_clear()
    yield
    config.get_settings.cache_clear()
    db.get_engine.cache_clear()
    db.get_sessionmaker.cache_clear()


class FakeGitHub:
    """Answers the two GitHub calls with whatever user the test chooses."""

    def __init__(self):
        self.user = GitHubUser(id=1, login="someone", name=None, avatar_url=None)

    def exchange_code(self, code: str) -> str:
        return f"token-for-{code}"

    def fetch_user(self, token: str) -> GitHubUser:
        return self.user


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def github():
    return FakeGitHub()


@pytest.fixture
def app(session_factory, github):
    from infra_portal.main import create_app

    app = create_app()

    def override_db():
        with session_factory() as session:
            yield session

    app.dependency_overrides[db.get_db] = override_db
    app.dependency_overrides[get_github_client] = lambda: github
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def add_member(session_factory):
    def _add(login: str, **fields) -> Member:
        with session_factory() as session:
            member = Member(github_login=login.lower(), **fields)
            session.add(member)
            session.commit()
            return member

    return _add


@pytest.fixture
def sign_in(client, github):
    """Run the whole OAuth flow for a GitHub user and return the final response."""

    def _sign_in(login: str, github_id: int, name: str | None = None):
        github.user = GitHubUser(id=github_id, login=login, name=name, avatar_url=None)
        redirect = client.get("/auth/github/login", follow_redirects=False)
        # GitHub would echo the state back; the test reads it from the redirect.
        state = parse_qs(urlparse(redirect.headers["location"]).query)["state"][0]
        return client.get(
            "/auth/github/callback",
            params={"code": "abc", "state": state},
            follow_redirects=False,
        )

    return _sign_in


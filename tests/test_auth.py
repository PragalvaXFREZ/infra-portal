from urllib.parse import parse_qs, urlparse

from sqlalchemy import select

from infra_portal.models import Member


def test_login_redirects_to_github_with_state_and_callback(client):
    response = client.get("/auth/github/login", follow_redirects=False)

    assert response.status_code == 303
    location = urlparse(response.headers["location"])
    assert location.netloc == "github.com"
    query = parse_qs(location.query)
    assert query["client_id"] == ["test-client-id"]
    assert query["redirect_uri"] == ["http://testserver/auth/github/callback"]
    assert len(query["state"][0]) >= 32


def test_callback_without_a_started_sign_in_is_refused(client):
    response = client.get("/auth/github/callback", params={"code": "abc", "state": "forged"})

    assert response.status_code == 400


def test_callback_with_the_wrong_state_is_refused(client):
    client.get("/auth/github/login", follow_redirects=False)

    response = client.get("/auth/github/callback", params={"code": "abc", "state": "forged"})

    assert response.status_code == 400


def test_uninvited_user_is_refused(client, sign_in):
    response = sign_in("stranger", github_id=99)

    assert response.status_code == 403
    assert "does not have an invite" in response.text
    assert client.get("/api/me").status_code == 401


def test_invited_user_claims_the_invite(client, sign_in, add_member, session_factory):
    add_member("Alice")

    response = sign_in("alice", github_id=42, name="Alice A")

    assert response.status_code == 303
    assert client.get("/api/me").json() == {
        "github_login": "alice",
        "display_name": "Alice A",
        "role": "member",
    }
    with session_factory() as session:
        member = session.scalar(select(Member).where(Member.github_login == "alice"))
        assert member.github_id == 42
        assert member.last_login_at is not None


def test_renamed_github_account_still_matches_by_id(client, sign_in, add_member):
    add_member("old-name", github_id=7)

    response = sign_in("new-name", github_id=7)

    assert response.status_code == 303
    assert client.get("/api/me").json()["github_login"] == "new-name"


def test_someone_else_cannot_claim_an_invite_already_claimed(sign_in, add_member):
    add_member("alice", github_id=42)

    # A different GitHub account that now uses the login "alice".
    response = sign_in("alice", github_id=1000)

    assert response.status_code == 403


def test_bootstrap_admin_signs_in_without_an_invite(client, sign_in):
    response = sign_in("boss", github_id=1)

    assert response.status_code == 303
    assert client.get("/api/me").json()["role"] == "admin"


def test_logout_ends_the_session(client, sign_in):
    sign_in("boss", github_id=1)

    client.post("/auth/logout", follow_redirects=False)

    assert client.get("/api/me").status_code == 401


def test_removed_member_loses_access_with_a_valid_cookie(client, sign_in, add_member, session_factory):
    add_member("alice")
    sign_in("alice", github_id=42)
    with session_factory() as session:
        session.delete(session.scalar(select(Member).where(Member.github_login == "alice")))
        session.commit()

    assert client.get("/api/me").status_code == 401


def test_sign_in_without_oauth_configured_says_so(client, monkeypatch):
    from infra_portal import config

    monkeypatch.setenv("GITHUB_CLIENT_ID", "")
    config.get_settings.cache_clear()

    assert client.get("/auth/github/login", follow_redirects=False).status_code == 503

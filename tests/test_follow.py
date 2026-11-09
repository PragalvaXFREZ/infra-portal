import json
import re
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from infra_portal import config
from infra_portal.config import Settings
from infra_portal.mail import (
    Email,
    MailError,
    NewPost,
    ResendMailer,
    get_mailer,
    new_post_emails,
)
from infra_portal.models import Follow, Post


class FakeMailer:
    def __init__(self):
        self.sent: list[Email] = []
        self.fail = False

    def send(self, emails: list[Email]) -> None:
        if self.fail:
            raise MailError("down")
        self.sent.extend(emails)


@pytest.fixture
def mailer(app, monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("MAIL_FROM", "infra <posts@example.com>")
    config.get_settings.cache_clear()
    fake = FakeMailer()
    app.dependency_overrides[get_mailer] = lambda: fake
    return fake


@pytest.fixture
def alice(add_member, sign_in):
    add_member("alice")
    sign_in("alice", github_id=42, name="Alice")


def _token(email: Email) -> str:
    return re.search(r"/follow/confirm/([\w-]+)", email.text).group(1)


def _follow(client, login="alice", email="Reader@Example.com"):
    return client.post(f"/u/{login}/follow", data={"email": email})


def _confirmed_follower(client, mailer, login="alice", email="reader@example.com") -> str:
    _follow(client, login, email)
    token = _token(mailer.sent[-1])
    assert client.get(f"/follow/confirm/{token}").status_code == 200
    mailer.sent.clear()
    return token


def _publish(client, title="Hello Docker", post_id=None, action="publish"):
    url = f"/posts/{post_id}/edit" if post_id else "/write"
    response = client.post(url, data={"title": title, "body": "x", "action": action}, follow_redirects=False)
    assert response.status_code == 303, response.text
    return int(re.search(r"/posts/(\d+)/edit", response.headers["location"]).group(1))


def test_form_is_hidden_until_email_is_configured(client, alice):
    assert "Get an email when" not in client.get("/u/alice").text
    assert _follow(client).status_code == 404


def test_form_shows_on_profile_and_published_post(client, alice, mailer):
    assert "Get an email when Alice posts" in client.get("/u/alice").text
    _publish(client)
    assert "Get an email when Alice posts" in client.get("/u/alice/hello-docker").text


def test_follow_sends_one_confirmation_and_stores_a_pending_row(client, alice, mailer, session_factory):
    response = _follow(client)

    assert response.status_code == 200
    assert "Check your inbox" in response.text
    assert [e.to for e in mailer.sent] == ["reader@example.com"]
    assert "http://testserver/follow/confirm/" in mailer.sent[0].text
    with session_factory() as session:
        row = session.scalar(select(Follow))
        assert row.email == "reader@example.com" and not row.is_confirmed


def test_asking_again_the_same_day_sends_nothing(client, alice, mailer):
    _follow(client)
    _follow(client, email="reader@example.com")
    assert len(mailer.sent) == 1


def test_asking_again_after_a_day_sends_a_new_link(client, alice, mailer, session_factory):
    _follow(client)
    with session_factory() as session:
        row = session.scalar(select(Follow))
        row.confirm_sent_at = datetime.now(UTC) - timedelta(days=2)
        session.commit()
    _follow(client)
    assert len(mailer.sent) == 2


def test_an_already_confirmed_address_gets_the_same_page_and_no_email(client, alice, mailer):
    _confirmed_follower(client, mailer)
    response = _follow(client)
    assert "Check your inbox" in response.text
    assert mailer.sent == []


def test_new_addresses_over_the_hourly_limit_get_no_email(client, alice, mailer, monkeypatch):
    monkeypatch.setattr("infra_portal.follow.MAX_NEW_PER_HOUR", 3)
    for n in range(5):
        response = _follow(client, email=f"fresh{n}@example.com")
        assert "Check your inbox" in response.text
    assert len(mailer.sent) == 3


def test_bad_address_is_refused(client, alice, mailer, session_factory):
    response = _follow(client, email="not-an-address")
    assert response.status_code == 422
    assert mailer.sent == []
    with session_factory() as session:
        assert session.scalars(select(Follow)).all() == []


def test_filled_hidden_field_sends_nothing(client, alice, mailer, session_factory):
    response = client.post("/u/alice/follow", data={"email": "bot@example.com", "nickname": "x"})
    assert response.status_code == 200
    assert mailer.sent == []
    with session_factory() as session:
        assert session.scalars(select(Follow)).all() == []


def test_failed_confirmation_email_leaves_no_row(client, alice, mailer, session_factory):
    mailer.fail = True
    assert _follow(client).status_code == 502
    with session_factory() as session:
        assert session.scalars(select(Follow)).all() == []


def test_unknown_confirm_link_is_404(client, mailer):
    assert client.get("/follow/confirm/nope").status_code == 404


def test_publishing_emails_confirmed_followers_of_that_author_only(
    client, alice, add_member, mailer, session_factory
):
    add_member("bob", display_name="Bob", github_id=7)
    _confirmed_follower(client, mailer, "alice", "a-fan@example.com")
    _confirmed_follower(client, mailer, "bob", "b-fan@example.com")
    _follow(client, "alice", "pending@example.com")  # never confirmed
    mailer.sent.clear()

    _publish(client)

    assert [e.to for e in mailer.sent] == ["a-fan@example.com"]
    email = mailer.sent[0]
    assert email.subject == "Hello Docker"
    assert "http://testserver/u/alice/hello-docker" in email.text
    assert email.unsubscribe_url and email.unsubscribe_url.startswith("http://testserver/unsubscribe/")


def test_saving_a_draft_emails_nobody(client, alice, mailer):
    _confirmed_follower(client, mailer)
    _publish(client, action="save")
    assert mailer.sent == []


def test_publishing_again_after_unpublish_emails_nobody(client, alice, mailer):
    _confirmed_follower(client, mailer)
    post_id = _publish(client)
    assert len(mailer.sent) == 1

    _publish(client, post_id=post_id, action="unpublish")
    _publish(client, post_id=post_id, action="publish")
    _publish(client, post_id=post_id, action="save")

    assert len(mailer.sent) == 1


def test_publishing_a_saved_draft_emails_followers(client, alice, mailer, session_factory):
    _confirmed_follower(client, mailer)
    post_id = _publish(client, action="save")
    _publish(client, post_id=post_id, action="publish")
    assert len(mailer.sent) == 1
    with session_factory() as session:
        assert session.get(Post, post_id).notified_at is not None


def test_a_mail_failure_does_not_stop_the_publish(client, alice, mailer, session_factory):
    _confirmed_follower(client, mailer)
    mailer.fail = True
    post_id = _publish(client)
    with session_factory() as session:
        assert session.get(Post, post_id).is_published


def test_unsubscribe_link_asks_first_and_the_post_removes(client, alice, mailer, session_factory):
    token = _confirmed_follower(client, mailer)

    page = client.get(f"/unsubscribe/{token}")
    assert page.status_code == 200 and "reader@example.com" in page.text
    with session_factory() as session:
        assert session.scalar(select(Follow)) is not None

    # Mail apps' one-click unsubscribe posts exactly this body.
    done = client.post(f"/unsubscribe/{token}", data={"List-Unsubscribe": "One-Click"})
    assert done.status_code == 200 and "Unsubscribed" in done.text
    with session_factory() as session:
        assert session.scalar(select(Follow)) is None

    _publish(client)
    assert mailer.sent == []


def test_author_sees_follower_count(client, alice, mailer):
    _confirmed_follower(client, mailer)
    assert "1 person gets an email when you publish" in client.get("/me/posts").text


def test_new_post_email_escapes_the_title():
    settings = Settings(
        database_url="sqlite://", session_secret="x" * 32, base_url="https://infra.example", mail_from="a@b.c"
    )
    [email] = new_post_emails(
        settings,
        NewPost("Alice", "<b>Hi</b>", "https://infra.example/u/alice/hi", [("r@example.com", "https://u")]),
    )
    assert "<b>Hi</b>" not in email.html and "&lt;b&gt;Hi&lt;/b&gt;" in email.html
    assert "<b>Hi</b>" in email.text


def test_resend_mailer_sends_a_batch_with_unsubscribe_headers():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"data": []})

    settings = Settings(
        database_url="sqlite://", session_secret="x" * 32, resend_api_key="re_123", mail_from="infra <p@e.com>"
    )
    ResendMailer(settings, httpx.MockTransport(handler)).send(
        [Email("r@example.com", "S", "t", "<p>h</p>", unsubscribe_url="https://u/1")]
    )

    [request] = requests
    assert request.url == "https://api.resend.com/emails/batch"
    assert request.headers["Authorization"] == "Bearer re_123"
    [item] = json.loads(request.content)
    assert item["from"] == "infra <p@e.com>" and item["to"] == ["r@example.com"]
    assert item["headers"]["List-Unsubscribe"] == "<https://u/1>"
    assert item["headers"]["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


def test_resend_mailer_raises_on_an_error_answer():
    settings = Settings(
        database_url="sqlite://", session_secret="x" * 32, resend_api_key="re_123", mail_from="p@e.com"
    )
    transport = httpx.MockTransport(lambda r: httpx.Response(403, json={"message": "domain not verified"}))
    with pytest.raises(MailError, match="403"):
        ResendMailer(settings, transport).send([Email("r@example.com", "S", "t", "h")])

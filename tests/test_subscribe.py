import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from infra_portal.mail import Email
from infra_portal.models import Follow, Subscriber, SubscriberMute


def _subscribe(client, email="Reader@Example.com", **extra):
    return client.post("/subscribe", data={"email": email, **extra})


def _confirm_token(email: Email) -> str:
    return re.search(r"/subscribe/confirm/([\w-]+)", email.text).group(1)


def _subscriber(client, mailer, email="reader@example.com") -> str:
    """Subscribe and confirm. Returns the token."""
    _subscribe(client, email)
    token = _confirm_token(mailer.sent[-1])
    assert client.get(f"/subscribe/confirm/{token}").status_code == 200
    mailer.sent.clear()
    return token


def _rows(session_factory, model):
    with session_factory() as session:
        return session.scalars(select(model)).all()


def test_forms_are_hidden_until_email_is_configured(client):
    assert "Get an email when anyone posts" not in client.get("/").text
    assert "Get an email when anyone posts" not in client.get("/writing").text
    assert _subscribe(client).status_code == 404


def test_form_shows_on_home_and_writing(client, mailer):
    assert "Get an email when anyone posts" in client.get("/").text
    assert "Get an email when anyone posts" in client.get("/writing").text


def test_subscribe_sends_one_confirmation_and_stores_a_pending_row(client, mailer, session_factory):
    response = _subscribe(client)

    assert response.status_code == 200 and "Check your inbox" in response.text
    [email] = mailer.sent
    assert email.to == "reader@example.com"
    assert "http://testserver/subscribe/confirm/" in email.text
    assert email.unsubscribe_url is None
    [row] = _rows(session_factory, Subscriber)
    assert row.email == "reader@example.com" and not row.is_confirmed


def test_confirm_link_starts_the_subscription(client, mailer, session_factory):
    _subscribe(client)
    page = client.get(f"/subscribe/confirm/{_confirm_token(mailer.sent[0])}")
    assert page.status_code == 200 and "You're subscribed" in page.text
    [row] = _rows(session_factory, Subscriber)
    assert row.is_confirmed


def test_asking_again_the_same_day_sends_nothing(client, mailer):
    _subscribe(client)
    _subscribe(client, "reader@example.com")
    assert len(mailer.sent) == 1


def test_asking_again_after_a_day_sends_a_new_link(client, mailer, session_factory):
    _subscribe(client)
    with session_factory() as session:
        row = session.scalar(select(Subscriber))
        row.confirm_sent_at = datetime.now(UTC) - timedelta(days=2)
        session.commit()
    _subscribe(client)
    assert len(mailer.sent) == 2


def test_known_and_unknown_addresses_get_the_same_page(client, mailer):
    _subscriber(client, mailer, "known@example.com")

    known = _subscribe(client, "known@example.com")
    unknown = _subscribe(client, "unknown@example.com")

    assert known.status_code == unknown.status_code == 200
    assert known.text.replace("known@", "X@") == unknown.text.replace("unknown@", "X@")
    assert [e.to for e in mailer.sent] == ["unknown@example.com"]


def test_new_addresses_over_the_hourly_limit_get_no_email(client, mailer, monkeypatch):
    monkeypatch.setattr("infra_portal.subscribe.MAX_NEW_PER_HOUR", 3)
    for n in range(5):
        response = _subscribe(client, f"fresh{n}@example.com")
        assert "Check your inbox" in response.text
    assert len(mailer.sent) == 3


def test_the_hourly_limit_does_not_count_follow_requests(client, mailer, add_member, monkeypatch):
    monkeypatch.setattr("infra_portal.subscribe.MAX_NEW_PER_HOUR", 1)
    add_member("alice", github_id=42)
    client.post("/u/alice/follow", data={"email": "fan@example.com"})
    _subscribe(client, "reader@example.com")
    assert [e.to for e in mailer.sent] == ["fan@example.com", "reader@example.com"]


def test_bad_address_is_refused(client, mailer, session_factory):
    response = _subscribe(client, "not-an-address")
    assert response.status_code == 422 and "Get an email when anyone posts" in response.text
    assert mailer.sent == [] and _rows(session_factory, Subscriber) == []


def test_filled_hidden_field_sends_nothing(client, mailer, session_factory):
    response = _subscribe(client, "bot@example.com", nickname="x")
    assert response.status_code == 200 and "Check your inbox" in response.text
    assert mailer.sent == [] and _rows(session_factory, Subscriber) == []


def test_failed_confirmation_email_leaves_no_row(client, mailer, session_factory):
    mailer.fail = True
    assert _subscribe(client).status_code == 502
    assert _rows(session_factory, Subscriber) == []


def test_unknown_tokens_answer_calmly(client, mailer):
    assert client.get("/subscribe/confirm/nope").status_code == 404
    for response in (
        client.get("/subscription/nope?author=1"),
        client.post("/subscription/nope", data={"List-Unsubscribe": "One-Click"}),
        client.post("/subscription/nope/mute", data={"author": "1"}),
        client.post("/subscription/nope/unmute", data={"author": "1"}),
        client.get("/subscription/" + "x" * 300),
    ):
        assert response.status_code == 200 and "Unsubscribed" in response.text


def test_subscription_page_changes_nothing(client, mailer, add_member, session_factory):
    alice = add_member("alice", display_name="Alice", github_id=42)
    token = _subscriber(client, mailer)

    page = client.get(f"/subscription/{token}?author={alice.id}")

    assert page.status_code == 200
    assert "reader@example.com" in page.text
    assert "Stop emails about Alice" in page.text and "Stop all emails" in page.text
    [row] = _rows(session_factory, Subscriber)
    assert row.is_confirmed
    assert _rows(session_factory, SubscriberMute) == []


def test_subscription_page_without_a_known_author_offers_only_stop_all(client, mailer):
    token = _subscriber(client, mailer)
    for query in ("", "?author=999", "?author=abc"):
        page = client.get(f"/subscription/{token}{query}")
        assert page.status_code == 200
        assert "Stop all emails" in page.text and "Stop emails about" not in page.text


def test_one_click_post_removes_the_whole_subscription(client, mailer, add_member, session_factory):
    alice = add_member("alice", github_id=42)
    bob = add_member("bob", github_id=7)
    token = _subscriber(client, mailer)
    client.post(f"/subscription/{token}/mute", data={"author": str(bob.id)})
    with session_factory() as session:
        # Another follow of the same address, and one of a different address.
        session.add(Follow(author_id=alice.id, email="reader@example.com", token="f1", confirmed_at=datetime.now(UTC)))
        session.add(Follow(author_id=alice.id, email="other@example.com", token="f2", confirmed_at=datetime.now(UTC)))
        session.commit()

    # Mail apps' one-click unsubscribe posts exactly this body, to the URL in the header, which
    # names an author. It still removes everything.
    done = client.post(f"/subscription/{token}?author={alice.id}", data={"List-Unsubscribe": "One-Click"})

    assert done.status_code == 200 and "Unsubscribed" in done.text
    assert _rows(session_factory, Subscriber) == []
    assert _rows(session_factory, SubscriberMute) == []
    assert [f.email for f in _rows(session_factory, Follow)] == ["other@example.com"]


def test_mute_lists_the_member_and_unmute_starts_them_again(client, mailer, add_member, session_factory):
    alice = add_member("alice", display_name="Alice", github_id=42)
    token = _subscriber(client, mailer)

    muted = client.post(f"/subscription/{token}/mute", data={"author": str(alice.id)})
    assert muted.status_code == 200
    assert "No more emails about Alice" in muted.text and "Start again" in muted.text
    assert "Stop emails about Alice" not in muted.text
    [mute] = _rows(session_factory, SubscriberMute)
    assert mute.author_id == alice.id
    # Muting twice keeps one row.
    client.post(f"/subscription/{token}/mute", data={"author": str(alice.id)})
    assert len(_rows(session_factory, SubscriberMute)) == 1

    again = client.post(f"/subscription/{token}/unmute", data={"author": str(alice.id)})
    assert again.status_code == 200 and "start again with their next post" in again.text
    assert _rows(session_factory, SubscriberMute) == []
    assert [s.email for s in _rows(session_factory, Subscriber)] == ["reader@example.com"]


def test_muting_a_member_also_removes_a_follow_of_them(client, mailer, add_member, session_factory):
    alice = add_member("alice", github_id=42)
    bob = add_member("bob", github_id=7)
    token = _subscriber(client, mailer)
    with session_factory() as session:
        session.add(Follow(author_id=alice.id, email="reader@example.com", token="fa", confirmed_at=datetime.now(UTC)))
        session.add(Follow(author_id=bob.id, email="reader@example.com", token="fb", confirmed_at=datetime.now(UTC)))
        session.commit()

    client.post(f"/subscription/{token}/mute", data={"author": str(alice.id)})

    assert [f.author_id for f in _rows(session_factory, Follow)] == [bob.id]


def test_old_follow_tokens_still_work(client, mailer, add_member, session_factory):
    add_member("alice", display_name="Alice", github_id=42)
    _subscriber(client, mailer)
    client.post("/u/alice/follow", data={"email": "reader@example.com"})
    follow_token = re.search(r"/follow/confirm/([\w-]+)", mailer.sent[-1].text).group(1)

    assert client.get(f"/follow/confirm/{follow_token}").status_code == 200
    page = client.get(f"/unsubscribe/{follow_token}")
    assert page.status_code == 200 and "Stop sending" in page.text
    done = client.post(f"/unsubscribe/{follow_token}", data={"List-Unsubscribe": "One-Click"})
    assert "Unsubscribed" in done.text
    assert _rows(session_factory, Follow) == []
    # The subscription is a separate thing and stays.
    assert len(_rows(session_factory, Subscriber)) == 1

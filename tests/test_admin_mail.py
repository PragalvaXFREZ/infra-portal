import html

import pytest
from markupsafe import escape

from infra_portal.config import Settings
from infra_portal.mail import sample_emails

HOSTILE = '<b>"Tom" & Jerry</b>'


def _settings(base_url: str = "https://infra.example") -> Settings:
    return Settings(database_url="sqlite://", session_secret="x" * 32, base_url=base_url)


def test_every_sample_has_a_subject_text_and_html():
    samples = sample_emails(_settings())
    assert [label for label, _ in samples] == [
        "Confirm a follow",
        "Confirm a subscription",
        "New post, to a follower",
        "New post, to a subscriber",
    ]
    for _, email in samples:
        assert email.to == "reader@example.com"
        assert email.subject.strip() and email.text.strip() and email.html.strip()
        assert "\n" not in email.subject
        assert email.html.startswith("<!doctype html>")


def test_sample_values_are_escaped_in_the_html_and_kept_in_the_text():
    samples = sample_emails(_settings(), author_name=HOSTILE, title=HOSTILE)
    # The subscription confirmation names no member, so it has nothing to escape.
    assert sum(HOSTILE in email.text for _, email in samples) == 3
    for _, email in samples:
        assert HOSTILE not in email.html and "<b>" not in email.html
        if HOSTILE in email.text:
            assert html.escape(HOSTILE) in email.html


def test_a_line_break_in_a_title_does_not_reach_the_subject():
    for _, email in sample_emails(_settings(), author_name="Asha\r\nBcc: x", title="One\ntwo"):
        assert "\n" not in email.subject and "\r" not in email.subject


def test_post_emails_carry_the_unsubscribe_headers_and_confirmations_do_not():
    confirm_follow, confirm_subscription, to_follower, to_subscriber = (e for _, e in sample_emails(_settings()))
    assert confirm_follow.headers == {} and confirm_subscription.headers == {}
    for email in (to_follower, to_subscriber):
        assert email.headers == {
            "List-Unsubscribe": f"<{email.unsubscribe_url}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        }
        # The same link is in the body, for mail apps that offer no button.
        assert email.unsubscribe_url in email.text
        assert html.escape(email.unsubscribe_url) in email.html
    assert "/unsubscribe/" in to_follower.unsubscribe_url
    assert "/subscription/" in to_subscriber.unsubscribe_url
    assert "You get this because you follow" in to_follower.text
    assert "You get this because you subscribed" in to_subscriber.text


def test_signed_out_visitor_gets_401(client):
    assert client.get("/admin/mail").status_code == 401
    assert client.post("/admin/mail/send", data={"to": "me@example.com"}).status_code == 401


def test_member_gets_403(client, add_member, sign_in, mailer):
    add_member("alice")
    sign_in("alice", github_id=42)
    assert client.get("/admin/mail").status_code == 403
    assert client.post("/admin/mail/send", data={"to": "me@example.com"}).status_code == 403
    assert mailer.sent == []
    assert "/admin/mail" not in client.get("/").text


@pytest.fixture
def boss(sign_in):
    sign_in("boss", github_id=1)


def test_admin_sees_every_message(client, boss):
    page = client.get("/admin/mail")
    assert page.status_code == 200
    for _, email in sample_emails(_settings(base_url="http://testserver")):
        assert str(escape(email.subject)) in page.text
        # The HTML part is inside the srcdoc attribute, escaped once more by the template.
        assert f'srcdoc="{escape(email.html)}"' in page.text
    assert page.text.count('<iframe sandbox=""') == 4
    assert "/admin/mail" in client.get("/").text


def test_without_email_the_page_says_so_and_sending_is_404(client, boss):
    page = client.get("/admin/mail")
    assert "Email is not configured" in page.text and "Send samples" not in page.text
    assert client.post("/admin/mail/send", data={"to": "me@example.com"}).status_code == 404


def test_admin_sends_the_samples_to_one_address(client, boss, mailer):
    response = client.post("/admin/mail/send", data={"to": " Me@Example.com "})
    assert response.status_code == 200 and "Sent the four samples" in response.text
    assert [e.to for e in mailer.sent] == ["me@example.com"] * 4


def test_sending_to_a_bad_address_sends_nothing(client, boss, mailer):
    assert client.post("/admin/mail/send", data={"to": "nope"}).status_code == 422
    assert mailer.sent == []


def test_a_mail_failure_is_shown(client, boss, mailer):
    mailer.fail = True
    response = client.post("/admin/mail/send", data={"to": "me@example.com"})
    assert response.status_code == 502 and "go out" in response.text

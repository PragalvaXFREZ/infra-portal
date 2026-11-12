"""Sending email through Resend's HTTP API.

The app sends four messages, all written here: the confirmation link for following one member, the
confirmation link for subscribing to every member's posts, and the new-post note in two versions,
one for a follower and one for a subscriber. `sample_emails` builds all four from made-up data for
the admin preview page and the tests.
"""

import html
import logging
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlparse

import httpx

from infra_portal.config import Settings, get_settings

log = logging.getLogger(__name__)

API_URL = "https://api.resend.com"
BATCH_SIZE = 100  # the most Resend takes in one batch request


class MailError(Exception):
    pass


@dataclass(frozen=True)
class Email:
    to: str
    subject: str
    text: str
    html: str
    # Puts a List-Unsubscribe header on the message, so mail apps can offer their own button.
    unsubscribe_url: str | None = None

    @property
    def headers(self) -> dict[str, str]:
        """Extra headers. The one-click pair is what Gmail and Yahoo ask of bulk senders (RFC 8058)."""
        if not self.unsubscribe_url:
            return {}
        return {
            "List-Unsubscribe": f"<{self.unsubscribe_url}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        }


class Mailer(Protocol):
    def send(self, emails: list[Email]) -> None: ...


class ResendMailer:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self._settings = settings
        self._transport = transport

    def _payload(self, email: Email) -> dict[str, object]:
        payload: dict[str, object] = {
            "from": self._settings.mail_from,
            "to": [email.to],
            "subject": email.subject,
            "text": email.text,
            "html": email.html,
        }
        if email.headers:
            payload["headers"] = email.headers
        return payload

    def send(self, emails: list[Email]) -> None:
        if not emails:
            return
        if not self._settings.mail_enabled:
            raise MailError("Email is not configured.")
        headers = {"Authorization": f"Bearer {self._settings.resend_api_key}"}
        with httpx.Client(base_url=API_URL, timeout=15, transport=self._transport, headers=headers) as client:
            for start in range(0, len(emails), BATCH_SIZE):
                chunk = [self._payload(e) for e in emails[start : start + BATCH_SIZE]]
                response = client.post("/emails/batch", json=chunk)
                if response.is_error:
                    raise MailError(f"Resend answered {response.status_code}: {response.text[:300]}")


def get_mailer() -> Mailer:
    """FastAPI dependency. Tests replace it with a fake."""
    return ResendMailer(get_settings())




# ---------- the messages ----------

# The grey of the reason line. A little darker than the site's muted grey so small text still has
# more than 4.5:1 contrast on white, the WCAG minimum.
_QUIET = "#6b655e"


def _site_name(settings: Settings) -> str:
    return urlparse(settings.base_url).netloc or settings.base_url


def _one_line(text: str) -> str:
    # A subject must be one line. A title or name with a line break in it, sent by a crafted form
    # post, would otherwise make a broken message, and one bad message can fail a whole batch.
    return " ".join(text.split())


def _html(paragraphs: list[str]) -> str:
    """Plain paragraphs in a minimal document. Every value is escaped by the caller."""
    body = "".join(f'<p style="margin:0 0 16px">{p}</p>' for p in paragraphs)
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1"></head>'
        '<body style="margin:0;padding:24px 16px;background:#ffffff">'
        '<div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;'
        f'font-size:16px;line-height:1.55;color:#1c1a17;max-width:560px">{body}</div>'
        "</body></html>"
    )


def _link(url: str, label: str | None = None) -> str:
    return f'<a href="{html.escape(url)}" style="color:#2f6b5e">{html.escape(label or url)}</a>'


def _small(content: str) -> str:
    return f'<span style="color:{_QUIET};font-size:14px">{content}</span>'


_NOT_YOU = "If that wasn't you, ignore this email. Nothing is sent unless the link is opened."


def _confirm(to: str, subject: str, asked: str, confirm_url: str, button: str) -> Email:
    """`asked` is plain text: what the reader asked for, as one sentence."""
    text = f"{asked}\n\nTo start, open this link:\n{confirm_url}\n\n{_NOT_YOU}"
    return Email(
        to=to,
        subject=_one_line(subject),
        text=text,
        html=_html([html.escape(asked), _link(confirm_url, button), _small(html.escape(_NOT_YOU))]),
    )


def confirm_email(settings: Settings, to: str, author_name: str, confirm_url: str) -> Email:
    """Confirms following one member."""
    site = _site_name(settings)
    return _confirm(
        to,
        f"Confirm: get an email when {author_name} posts",
        f"Someone, probably you, asked for an email at this address whenever {author_name} posts on {site}.",
        confirm_url,
        "Yes, send me their posts",
    )


def subscription_confirm_email(settings: Settings, to: str, confirm_url: str) -> Email:
    """Confirms a subscription to every member's posts."""
    site = _site_name(settings)
    return _confirm(
        to,
        f"Confirm: get an email when anyone posts on {site}",
        f"Someone, probably you, asked for an email at this address whenever any member posts on {site}.",
        confirm_url,
        "Yes, send me new posts",
    )


def subscription_url(settings: Settings, token: str, author_id: int) -> str:
    """The page where a subscriber stops one member's posts or all of them. See subscribe.py.

    The author is in the URL so the page knows which member the email was about.
    """
    return f"{settings.base_url}/subscription/{token}?author={author_id}"


@dataclass(frozen=True)
class NewPost:
    """What the new-post emails need, copied out of the database before the session closes."""

    author_name: str
    title: str
    url: str
    # (email, unsubscribe URL) for each confirmed follower of the author.
    recipients: list[tuple[str, str]]
    # (email, subscription page URL) for each confirmed subscriber who has not muted the author.
    # No address is in both lists.
    subscribers: list[tuple[str, str]] = field(default_factory=list)


def _new_post_email(
    to: str, subject: str, post: NewPost, reason: str, stop_label: str, stop_url: str
) -> Email:
    text = (
        f"{post.author_name} published a new post:\n\n"
        f"{post.title}\n{post.url}\n\n"
        f"{reason}\n"
        f"{stop_label}: {stop_url}"
    )
    return Email(
        to=to,
        subject=subject,
        text=text,
        html=_html(
            [
                f"{html.escape(post.author_name)} published a new post:",
                f'<strong style="font-size:18px">{_link(post.url, post.title)}</strong>',
                _small(f"{html.escape(reason)} {_link(stop_url, stop_label)}."),
            ]
        ),
        unsubscribe_url=stop_url,
    )


def new_post_emails(settings: Settings, post: NewPost) -> list[Email]:
    site = _site_name(settings)
    # The author's name first: a subscriber gets posts from many members.
    subject = _one_line(f"New post by {post.author_name}: {post.title}")
    emails = [
        _new_post_email(
            to,
            subject,
            post,
            f"You get this because you follow {post.author_name} on {site}.",
            "Stop these emails",
            url,
        )
        for to, url in post.recipients
    ]
    emails += [
        _new_post_email(
            to,
            subject,
            post,
            f"You get this because you subscribed to new posts on {site}.",
            f"Stop emails about {post.author_name}, or all emails",
            url,
        )
        for to, url in post.subscribers
    ]
    return emails


def send_new_post(mailer: Mailer, settings: Settings, post: NewPost) -> None:
    """Runs after the response is sent. A failure is logged; the post stays published."""
    try:
        mailer.send(new_post_emails(settings, post))
    except Exception:
        log.exception(
            "Could not email %d readers about %s", len(post.recipients) + len(post.subscribers), post.url
        )


# ---------- samples for the preview page ----------


def sample_emails(
    settings: Settings,
    to: str = "reader@example.com",
    author_name: str = "Asha Rai",
    title: str = "Rolling updates without dropped requests",
) -> list[tuple[str, Email]]:
    """Each of the four messages, built by the same code as the real ones, with made-up data.

    The links use tokens that match no row, so opening one changes nothing.
    """
    base = settings.base_url
    token = "sample-token-not-real"
    post = NewPost(
        author_name=author_name,
        title=title,
        url=f"{base}/u/sample/rolling-updates",
        recipients=[(to, f"{base}/unsubscribe/{token}")],
        subscribers=[(to, subscription_url(settings, token, 0))],
    )
    to_follower, to_subscriber = new_post_emails(settings, post)
    return [
        ("Confirm a follow", confirm_email(settings, to, author_name, f"{base}/follow/confirm/{token}")),
        ("Confirm a subscription", subscription_confirm_email(settings, to, f"{base}/subscribe/confirm/{token}")),
        ("New post, to a follower", to_follower),
        ("New post, to a subscriber", to_subscriber),
    ]

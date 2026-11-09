"""Sending email through Resend's HTTP API.

The app sends two kinds of email: the confirmation link when someone follows a member, and the
note to confirmed followers when that member publishes a post. Both are written here.
"""

import html
import logging
from dataclasses import dataclass
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
        if email.unsubscribe_url:
            payload["headers"] = {
                "List-Unsubscribe": f"<{email.unsubscribe_url}>",
                "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
            }
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


def _site_name(settings: Settings) -> str:
    return urlparse(settings.base_url).netloc or settings.base_url


def _html(paragraphs: list[str]) -> str:
    """Plain paragraphs. Every value is escaped by the caller."""
    body = "".join(f'<p style="margin:0 0 16px">{p}</p>' for p in paragraphs)
    return (
        '<div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;'
        f'font-size:16px;line-height:1.55;color:#1c1a17;max-width:560px">{body}</div>'
    )


def _link(url: str, label: str | None = None) -> str:
    return f'<a href="{html.escape(url)}" style="color:#2f6b5e">{html.escape(label or url)}</a>'


def confirm_email(settings: Settings, to: str, author_name: str, confirm_url: str) -> Email:
    site = _site_name(settings)
    text = (
        f"Someone, probably you, asked for an email at this address whenever {author_name} "
        f"posts on {site}.\n\n"
        f"To start, open this link:\n{confirm_url}\n\n"
        "If that wasn't you, ignore this email. Nothing more will be sent."
    )
    return Email(
        to=to,
        subject=f"Confirm emails from {author_name}",
        text=text,
        html=_html(
            [
                f"Someone, probably you, asked for an email at this address whenever "
                f"{html.escape(author_name)} posts on {html.escape(site)}.",
                _link(confirm_url, "Yes, send me their posts"),
                "If that wasn't you, ignore this email. Nothing more will be sent.",
            ]
        ),
    )


@dataclass(frozen=True)
class NewPost:
    """What a follower email needs, copied out of the database before the session closes."""

    author_name: str
    title: str
    url: str
    # (email, unsubscribe URL) for each confirmed follower.
    recipients: list[tuple[str, str]]


def new_post_emails(settings: Settings, post: NewPost) -> list[Email]:
    site = _site_name(settings)
    emails = []
    for to, unsubscribe_url in post.recipients:
        text = (
            f"{post.author_name} published a new post:\n\n"
            f"{post.title}\n{post.url}\n\n"
            f"You get this because you follow {post.author_name} on {site}.\n"
            f"Stop these emails: {unsubscribe_url}"
        )
        emails.append(
            Email(
                to=to,
                subject=post.title,
                text=text,
                html=_html(
                    [
                        f"{html.escape(post.author_name)} published a new post:",
                        f'<strong style="font-size:18px">{_link(post.url, post.title)}</strong>',
                        f'<span style="color:#7d7770;font-size:14px">You get this because you follow '
                        f"{html.escape(post.author_name)} on {html.escape(site)}. "
                        f"{_link(unsubscribe_url, 'Stop these emails')}.</span>",
                    ]
                ),
                unsubscribe_url=unsubscribe_url,
            )
        )
    return emails


def send_new_post(mailer: Mailer, settings: Settings, post: NewPost) -> None:
    """Runs after the response is sent. A failure is logged; the post stays published."""
    try:
        mailer.send(new_post_emails(settings, post))
    except Exception:
        log.exception("Could not email %d followers about %s", len(post.recipients), post.url)

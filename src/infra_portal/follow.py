"""Following a member by email: the form, the confirmation link and unsubscribing.

The pages answer the same way whether or not the address already follows the member, so the
form cannot be used to find out who follows whom.

`announcement` is here too. It picks who gets the email about a new post: the author's followers
and the subscribers to every member's posts (see subscribe.py).
"""

import logging
import re
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from infra_portal.auth import current_member
from infra_portal.config import Settings, get_settings
from infra_portal.db import get_db
from infra_portal.mail import (
    Mailer,
    NewPost,
    confirm_email,
    get_mailer,
    subscription_url,
)
from infra_portal.models import Follow, Member, Post, Subscriber, SubscriberMute
from infra_portal.profiles import get_member_by_login
from infra_portal.templating import templates

log = logging.getLogger(__name__)

router = APIRouter()

MAX_EMAIL = 254
# Enough to catch typing mistakes. Whether the address is real is settled by the confirmation email.
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# A second request for the same address within this time sends no new confirmation email.
RESEND_AFTER = timedelta(days=1)
# At most this many confirmation emails to new addresses per member per hour. Each one is sent
# from the site's domain, so a script feeding the form fresh addresses must not get far.
MAX_NEW_PER_HOUR = 20


def _aware(moment: datetime | None) -> datetime | None:
    # SQLite, used in tests, returns naive datetimes. They are UTC.
    if moment is not None and moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment


def _page(
    request: Request, member: Member | None, name: str, context: dict, status_code: int = status.HTTP_200_OK
):
    return templates.TemplateResponse(request, name, {"member": member, **context}, status_code=status_code)


def _recent_pending(db: Session, author_id: int, now: datetime) -> int:
    return db.scalar(
        select(func.count())
        .select_from(Follow)
        .where(
            Follow.author_id == author_id,
            Follow.confirmed_at.is_(None),
            Follow.confirm_sent_at > now - timedelta(hours=1),
        )
    ) or 0


def _by_token(db: Session, token: str) -> Follow | None:
    if len(token) > 64:
        return None
    return db.scalar(select(Follow).where(Follow.token == token))


@router.post("/u/{login}/follow")
def follow(
    login: str,
    request: Request,
    email: str = Form(""),
    nickname: str = Form(""),
    member: Member | None = Depends(current_member),
    db: Session = Depends(get_db),
    mailer: Mailer = Depends(get_mailer),
    settings: Settings = Depends(get_settings),
):
    if not settings.mail_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Following is not available.")
    author = get_member_by_login(db, login)
    email = email.strip().lower()
    if len(email) > MAX_EMAIL or not EMAIL_PATTERN.match(email):
        return _page(
            request,
            member,
            "follow_result.html",
            {"author": author, "outcome": "invalid", "email": email},
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    # `nickname` is a field people never see. Only form-filling bots put text in it.
    if nickname:
        return _page(request, member, "follow_result.html", {"author": author, "outcome": "sent", "email": email})

    row = db.scalar(select(Follow).where(Follow.author_id == author.id, Follow.email == email))
    now = datetime.now(UTC)
    if row is None and _recent_pending(db, author.id, now) >= MAX_NEW_PER_HOUR:
        log.warning("Follow requests for %s are over the hourly limit; sending nothing", author.github_login)
        return _page(request, member, "follow_result.html", {"author": author, "outcome": "sent", "email": email})
    if row is None:
        row = Follow(author_id=author.id, email=email, token=secrets.token_urlsafe(32))
        db.add(row)
    sent_at = _aware(row.confirm_sent_at)
    if not row.is_confirmed and (sent_at is None or now - sent_at > RESEND_AFTER):
        row.confirm_sent_at = now
        confirm_url = f"{settings.base_url}/follow/confirm/{row.token}"
        try:
            mailer.send([confirm_email(settings, email, author.name, confirm_url)])
        except Exception:
            log.exception("Could not send a confirmation email")
            db.rollback()
            return _page(
                request,
                member,
                "follow_result.html",
                {"author": author, "outcome": "failed", "email": email},
                status.HTTP_502_BAD_GATEWAY,
            )
        db.commit()
    return _page(request, member, "follow_result.html", {"author": author, "outcome": "sent", "email": email})


@router.get("/follow/confirm/{token}")
def confirm(
    token: str,
    request: Request,
    member: Member | None = Depends(current_member),
    db: Session = Depends(get_db),
):
    row = _by_token(db, token)
    if row is None:
        return _page(request, member, "follow_result.html", {"outcome": "unknown"}, status.HTTP_404_NOT_FOUND)
    if row.confirmed_at is None:
        row.confirmed_at = datetime.now(UTC)
        # Asking for this member's posts again undoes an earlier "not this member" on a subscription
        # of the same address. Otherwise the mute would win and the follow would send nothing.
        db.execute(
            delete(SubscriberMute).where(
                SubscriberMute.author_id == row.author_id,
                SubscriberMute.subscriber_id.in_(select(Subscriber.id).where(Subscriber.email == row.email)),
            )
        )
        db.commit()
    return _page(request, member, "follow_result.html", {"author": row.author, "outcome": "confirmed", "email": row.email})


@router.get("/unsubscribe/{token}")
def unsubscribe_page(
    token: str,
    request: Request,
    member: Member | None = Depends(current_member),
    db: Session = Depends(get_db),
):
    # Only shows a button. Mail scanners open links, and must not unsubscribe anyone by doing so.
    row = _by_token(db, token)
    if row is None:
        return _page(request, member, "follow_result.html", {"outcome": "unsubscribed"})
    return _page(request, member, "unsubscribe.html", {"author": row.author, "email": row.email, "token": token})


@router.post("/unsubscribe/{token}")
def unsubscribe(
    token: str,
    request: Request,
    member: Member | None = Depends(current_member),
    db: Session = Depends(get_db),
):
    # Also the target of mail apps' one-click unsubscribe (RFC 8058), which posts here with
    # List-Unsubscribe=One-Click in the body.
    row = _by_token(db, token)
    author = row.author if row else None
    if row is not None:
        db.delete(row)
        db.commit()
    return _page(request, member, "follow_result.html", {"author": author, "outcome": "unsubscribed"})


def _audience(db: Session, author_id: int) -> tuple[list[Follow], list[Subscriber]]:
    """Who gets an email when this member publishes, with each address once.

    Confirmed subscribers who have not muted the member, and confirmed followers whose address is
    not among them. An address that is both gets the subscriber email, because its link manages
    everything. A mute wins over a follow of the same address.
    """
    muting = select(SubscriberMute.subscriber_id).where(SubscriberMute.author_id == author_id)
    subscribers = db.scalars(
        select(Subscriber)
        .where(Subscriber.confirmed_at.is_not(None), Subscriber.id.not_in(muting))
        .order_by(Subscriber.id)
    ).all()
    muted_emails = select(Subscriber.email).where(Subscriber.id.in_(muting))
    followers = db.scalars(
        select(Follow)
        .where(
            Follow.author_id == author_id,
            Follow.confirmed_at.is_not(None),
            Follow.email.not_in(muted_emails),
        )
        .order_by(Follow.id)
    ).all()
    taken = {s.email for s in subscribers}
    return [f for f in followers if f.email not in taken], list(subscribers)


def follower_count(db: Session, author_id: int) -> int:
    """How many addresses get an email when this member publishes: followers and subscribers."""
    followers, subscribers = _audience(db, author_id)
    return len(followers) + len(subscribers)


def announcement(db: Session, settings: Settings, post: Post, author: Member) -> NewPost | None:
    """Mark a just-published post as announced and return what its emails need.

    Returns None when the post was announced before, email is off, or nobody gets an email. Call
    it before the commit: it sets `notified_at` in the same transaction, and copies everything out
    of the session, because the email is sent after the response, when the session is closed.
    Only posts are announced. Nothing else on the site emails followers or subscribers.
    """
    if post.notified_at is not None or not post.is_published:
        return None
    post.notified_at = datetime.now(UTC)
    if not settings.mail_enabled:
        return None
    followers, subscribers = _audience(db, author.id)
    if not followers and not subscribers:
        return None
    return NewPost(
        author_name=author.name,
        title=post.title,
        url=f"{settings.base_url}/u/{author.github_login}/{post.slug}",
        recipients=[(f.email, f"{settings.base_url}/unsubscribe/{f.token}") for f in followers],
        subscribers=[(s.email, subscription_url(settings, s.token, author.id)) for s in subscribers],
    )

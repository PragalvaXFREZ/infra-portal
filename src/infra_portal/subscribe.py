"""Subscribing to every member's posts by email, and the page that manages a subscription.

It works like following one member (see follow.py): a confirmation link first, a hidden field for
bots, at most one confirmation email a day per address, and pages that answer the same whether or
not the address is already subscribed.
"""

import logging
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from infra_portal.auth import current_member
from infra_portal.config import Settings, get_settings
from infra_portal.db import get_db
from infra_portal.follow import EMAIL_PATTERN, MAX_EMAIL, RESEND_AFTER, _aware, _page
from infra_portal.mail import Mailer, get_mailer, subscription_confirm_email
from infra_portal.models import Follow, Member, Subscriber, SubscriberMute

log = logging.getLogger(__name__)

router = APIRouter()

# At most this many confirmation emails to new addresses per hour, for the whole site. The follow
# form's limit counts per member, so it does not cover this form. 20, the same number: a link
# shared in a class chat brings a burst of real sign-ups well under it, and a script feeding the
# form fresh addresses gets no more than 20 unwanted emails an hour out of the site's domain.
MAX_NEW_PER_HOUR = 20


def _recent_pending(db: Session, now: datetime) -> int:
    return db.scalar(
        select(func.count())
        .select_from(Subscriber)
        .where(Subscriber.confirmed_at.is_(None), Subscriber.confirm_sent_at > now - timedelta(hours=1))
    ) or 0


def _by_token(db: Session, token: str) -> Subscriber | None:
    if len(token) > 64:
        return None
    return db.scalar(select(Subscriber).where(Subscriber.token == token))


def _member(db: Session, value: str) -> Member | None:
    """The member named by an id from a URL or a form, where anything may arrive.

    Something that is not an id means no member. So does an invite nobody has claimed: the rest
    of the site does not show those either.
    """
    if not (value.isdecimal() and value.isascii() and len(value) < 10):
        return None
    person = db.get(Member, int(value))
    return person if person is not None and person.has_signed_in else None


def _result(request: Request, member: Member | None, outcome: str, email: str = "", status_code: int = 200):
    return _page(request, member, "subscribe_result.html", {"outcome": outcome, "email": email}, status_code)


@router.post("/subscribe")
def subscribe(
    request: Request,
    email: str = Form(""),
    nickname: str = Form(""),
    member: Member | None = Depends(current_member),
    db: Session = Depends(get_db),
    mailer: Mailer = Depends(get_mailer),
    settings: Settings = Depends(get_settings),
):
    if not settings.mail_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Subscribing is not available.")
    email = email.strip().lower()
    if len(email) > MAX_EMAIL or not EMAIL_PATTERN.match(email):
        return _result(request, member, "invalid", email, status.HTTP_422_UNPROCESSABLE_CONTENT)
    # `nickname` is a field people never see. Only form-filling bots put text in it.
    if nickname:
        return _result(request, member, "sent", email)

    row = db.scalar(select(Subscriber).where(Subscriber.email == email))
    now = datetime.now(UTC)
    if row is None and _recent_pending(db, now) >= MAX_NEW_PER_HOUR:
        log.warning("Subscribe requests are over the hourly limit; sending nothing")
        return _result(request, member, "sent", email)
    if row is None:
        row = Subscriber(email=email, token=secrets.token_urlsafe(32))
        db.add(row)
    sent_at = _aware(row.confirm_sent_at)
    if not row.is_confirmed and (sent_at is None or now - sent_at > RESEND_AFTER):
        row.confirm_sent_at = now
        confirm_url = f"{settings.base_url}/subscribe/confirm/{row.token}"
        try:
            mailer.send([subscription_confirm_email(settings, email, confirm_url)])
        except Exception:
            log.exception("Could not send a confirmation email")
            db.rollback()
            return _result(request, member, "failed", email, status.HTTP_502_BAD_GATEWAY)
        db.commit()
    return _result(request, member, "sent", email)


@router.get("/subscribe/confirm/{token}")
def confirm(
    token: str,
    request: Request,
    member: Member | None = Depends(current_member),
    db: Session = Depends(get_db),
):
    row = _by_token(db, token)
    if row is None:
        return _result(request, member, "unknown", status_code=status.HTTP_404_NOT_FOUND)
    if row.confirmed_at is None:
        row.confirmed_at = datetime.now(UTC)
        db.commit()
    return _page(
        request,
        member,
        "subscribe_result.html",
        {"outcome": "confirmed", "email": row.email, "token": row.token},
    )


def _manage(
    request: Request,
    member: Member | None,
    row: Subscriber,
    author: Member | None,
    done: str | None = None,
):
    muted = sorted((m.author for m in row.mutes), key=lambda person: person.name.lower())
    return _page(
        request,
        member,
        "subscription.html",
        {
            "email": row.email,
            "token": row.token,
            "confirmed": row.is_confirmed,
            "author": author,
            "author_muted": author is not None and author in muted,
            "muted_members": muted,
            "done": done,
        },
    )


@router.get("/subscription/{token}")
def subscription_page(
    token: str,
    request: Request,
    author: str = "",
    member: Member | None = Depends(current_member),
    db: Session = Depends(get_db),
):
    # Only shows buttons. Mail scanners open links, and must not change anything by doing so.
    # `author` is the member the email was about.
    row = _by_token(db, token)
    if row is None:
        return _result(request, member, "unsubscribed")
    return _manage(request, member, row, _member(db, author))


@router.post("/subscription/{token}")
def unsubscribe(
    token: str,
    request: Request,
    member: Member | None = Depends(current_member),
    db: Session = Depends(get_db),
):
    # Also the target of mail apps' one-click unsubscribe (RFC 8058), which posts here with
    # List-Unsubscribe=One-Click in the body. A mail app's button promises no more mail from this
    # sender, so this ends the subscription and every follow of the same address. Stopping one
    # member only is offered on the page, never here.
    row = _by_token(db, token)
    if row is not None:
        db.execute(delete(Follow).where(Follow.email == row.email))
        db.delete(row)
        db.commit()
    return _result(request, member, "unsubscribed")


@router.post("/subscription/{token}/mute")
def mute(
    token: str,
    request: Request,
    author: str = Form(""),
    member: Member | None = Depends(current_member),
    db: Session = Depends(get_db),
):
    row = _by_token(db, token)
    if row is None:
        return _result(request, member, "unsubscribed")
    target = _member(db, author)
    if target is None:
        return _manage(request, member, row, None)
    if all(m.author_id != target.id for m in row.mutes):
        row.mutes.append(SubscriberMute(author_id=target.id))
    # A follow of the same member would keep their posts coming, so it goes too.
    db.execute(delete(Follow).where(Follow.email == row.email, Follow.author_id == target.id))
    db.commit()
    return _manage(request, member, row, target, "muted")


@router.post("/subscription/{token}/unmute")
def unmute(
    token: str,
    request: Request,
    author: str = Form(""),
    member: Member | None = Depends(current_member),
    db: Session = Depends(get_db),
):
    row = _by_token(db, token)
    if row is None:
        return _result(request, member, "unsubscribed")
    target = _member(db, author)
    found = [m for m in row.mutes if target is not None and m.author_id == target.id]
    if not found:
        return _manage(request, member, row, target)
    for mute_row in found:
        row.mutes.remove(mute_row)
    db.commit()
    return _manage(request, member, row, target, "unmuted")

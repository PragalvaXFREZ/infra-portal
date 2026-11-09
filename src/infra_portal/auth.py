"""Sign-in with GitHub, sessions, and access checks.

Flow:
1. /auth/github/login stores a random `state` in the session and redirects to GitHub.
2. GitHub sends the browser back to /auth/github/callback with `code` and `state`.
3. The callback checks `state`, trades `code` for a token, reads the GitHub user,
   and signs in the matching member. Anyone without an invite is refused.
"""

import secrets
from datetime import UTC, datetime
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from infra_portal.config import Settings, get_settings
from infra_portal.db import get_db
from infra_portal.github import AUTHORIZE_URL, GitHubClient, GitHubUser, get_github_client
from infra_portal.models import ROLE_ADMIN, Member
from infra_portal.templating import templates

router = APIRouter(prefix="/auth")

SESSION_MEMBER_KEY = "member_id"
SESSION_STATE_KEY = "oauth_state"


def current_member(request: Request, db: Session = Depends(get_db)) -> Member | None:
    """The signed-in member, or None.

    The member is loaded from the database on every request, so removing a
    member ends their access at once, even with a valid cookie.
    """
    member_id = request.session.get(SESSION_MEMBER_KEY)
    if member_id is None:
        return None
    member = db.get(Member, member_id)
    if member is None:
        request.session.clear()
    return member


def require_member(member: Member | None = Depends(current_member)) -> Member:
    if member is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in first.")
    return member


def require_admin(member: Member = Depends(require_member)) -> Member:
    if not member.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admins only.")
    return member


@router.get("/github/login")
def github_login(request: Request, settings: Settings = Depends(get_settings)):
    if not settings.github_client_id:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "GitHub sign-in is not configured.")
    state = secrets.token_urlsafe(32)
    request.session[SESSION_STATE_KEY] = state
    query = urlencode(
        {
            "client_id": settings.github_client_id,
            "redirect_uri": settings.github_callback_url,
            "state": state,
            "allow_signup": "false",
        }
    )
    return RedirectResponse(f"{AUTHORIZE_URL}?{query}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/github/callback")
def github_callback(
    request: Request,
    code: str = "",
    state: str = "",
    db: Session = Depends(get_db),
    github: GitHubClient = Depends(get_github_client),
    settings: Settings = Depends(get_settings),
):
    # The state proves this callback answers a sign-in that this browser started.
    expected = request.session.pop(SESSION_STATE_KEY, None)
    if not code or not expected or not secrets.compare_digest(state, expected):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Sign-in expired or was not started here. Try again.")

    try:
        user = github.fetch_user(github.exchange_code(code))
    except Exception as exc:  # any GitHub or network failure ends the same way
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "GitHub sign-in failed. Try again.") from exc

    member = match_member(db, user, settings)
    if member is None:
        return templates.TemplateResponse(
            request,
            "not_invited.html",
            {"login": user.login, "member": None, "contact_email": settings.contact_email},
            status_code=status.HTTP_403_FORBIDDEN,
        )

    member.github_id = user.id
    member.github_login = user.login.lower()
    # GitHub fills the name once. After that the member edits it on their profile.
    if member.display_name is None:
        member.display_name = user.name
    member.avatar_url = user.avatar_url
    member.last_login_at = datetime.now(UTC)
    db.commit()

    # A fresh session on sign-in, so nothing from before sign-in carries over.
    request.session.clear()
    request.session[SESSION_MEMBER_KEY] = member.id
    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)


def match_member(db: Session, user: GitHubUser, settings: Settings) -> Member | None:
    # 1. A member who signed in before: match the permanent numeric ID.
    member = db.scalar(select(Member).where(Member.github_id == user.id))
    if member is not None:
        return member

    login = user.login.lower()

    # 2. An open invite for this login.
    member = db.scalar(
        select(Member).where(Member.github_login == login, Member.github_id.is_(None))
    )
    if member is not None:
        return member

    # 3. A bootstrap admin from the environment, so the first admin can get in.
    if login in settings.admin_github_logins:
        member = Member(github_login=login, role=ROLE_ADMIN)
        db.add(member)
        db.flush()
        return member

    return None


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)

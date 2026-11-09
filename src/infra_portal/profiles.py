"""Public member pages and profile editing."""

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from infra_portal.auth import current_member, require_member
from infra_portal.db import get_db
from infra_portal.models import STATUS_PUBLISHED, Member, Post
from infra_portal.rendering import render_markdown
from infra_portal.templating import templates

router = APIRouter()

MAX_NAME = 100
MAX_BIO = 2000
MAX_WEBSITE = 200


def get_member_by_login(db: Session, login: str) -> Member:
    member = db.scalar(
        select(Member).where(Member.github_login == login.lower(), Member.github_id.is_not(None))
    )
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such member.")
    return member


def signed_in_members(db: Session) -> list[Member]:
    """Members who have signed in at least once. Pending invites stay hidden."""
    return list(
        db.scalars(
            select(Member).where(Member.github_id.is_not(None)).order_by(Member.github_login)
        ).all()
    )


@router.get("/members")
def member_directory(
    request: Request,
    member: Member | None = Depends(current_member),
    db: Session = Depends(get_db),
):
    members = signed_in_members(db)
    return templates.TemplateResponse(
        request, "members.html", {"member": member, "members": members}
    )


@router.get("/u/{login}")
def profile(
    login: str,
    request: Request,
    member: Member | None = Depends(current_member),
    db: Session = Depends(get_db),
):
    person = get_member_by_login(db, login)
    posts = db.scalars(
        select(Post)
        .where(Post.author_id == person.id, Post.status == STATUS_PUBLISHED)
        .order_by(Post.published_at.desc())
    ).all()
    return templates.TemplateResponse(
        request,
        "profile.html",
        {"member": member, "person": person, "posts": posts, "bio_html": render_markdown(person.bio or "")},
    )


@router.get("/settings/profile")
def edit_profile(request: Request, member: Member = Depends(require_member)):
    return templates.TemplateResponse(request, "profile_edit.html", {"member": member, "error": None})


@router.post("/settings/profile")
def save_profile(
    request: Request,
    display_name: str = Form(""),
    bio: str = Form(""),
    website: str = Form(""),
    member: Member = Depends(require_member),
    db: Session = Depends(get_db),
):
    display_name, bio, website = display_name.strip(), bio.strip(), website.strip()
    error = None
    if len(display_name) > MAX_NAME:
        error = f"Name must be at most {MAX_NAME} characters."
    elif len(bio) > MAX_BIO:
        error = f"Bio must be at most {MAX_BIO} characters."
    elif len(website) > MAX_WEBSITE:
        error = f"Website must be at most {MAX_WEBSITE} characters."
    elif website and not website.startswith(("https://", "http://")):
        # Only web links: a javascript: URL in an href would run on click.
        error = "Website must start with https:// or http://."
    if error:
        return templates.TemplateResponse(
            request,
            "profile_edit.html",
            {"member": member, "error": error},
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    member.display_name = display_name or None
    member.bio = bio or None
    member.website = website or None
    db.add(member)
    db.commit()
    return RedirectResponse(f"/u/{member.github_login}", status_code=status.HTTP_303_SEE_OTHER)

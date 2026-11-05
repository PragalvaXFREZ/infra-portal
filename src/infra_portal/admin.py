"""Member management for admins: invite and remove."""

import re

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from infra_portal.auth import require_admin
from infra_portal.db import get_db
from infra_portal.models import Member
from infra_portal.templating import templates

router = APIRouter(prefix="/admin")

# GitHub's rule: 1 to 39 characters, letters, digits and single hyphens,
# not starting or ending with a hyphen.
GITHUB_LOGIN = re.compile(r"^[a-z\d](?:[a-z\d]|-(?=[a-z\d])){0,38}$")


@router.get("/members")
def list_members(
    request: Request,
    admin: Member = Depends(require_admin),
    db: Session = Depends(get_db),
):
    members = db.scalars(select(Member).order_by(Member.github_login)).all()
    return templates.TemplateResponse(
        request, "admin_members.html", {"member": admin, "members": members}
    )


@router.post("/members")
def invite_member(
    github_login: str = Form(...),
    admin: Member = Depends(require_admin),
    db: Session = Depends(get_db),
):
    login = github_login.strip().lstrip("@").lower()
    if not GITHUB_LOGIN.match(login):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "That is not a valid GitHub login.")
    if db.scalar(select(Member).where(Member.github_login == login)) is None:
        db.add(Member(github_login=login))
        db.commit()
    return RedirectResponse("/admin/members", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/members/{member_id}/remove")
def remove_member(
    member_id: int,
    admin: Member = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if member_id == admin.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot remove yourself.")
    member = db.get(Member, member_id)
    if member is not None:
        db.delete(member)
        db.commit()
    return RedirectResponse("/admin/members", status_code=status.HTTP_303_SEE_OTHER)

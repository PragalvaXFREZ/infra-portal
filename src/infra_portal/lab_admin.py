"""Admin pages for database labs: list, write, preview, change visibility, delete.

These are the pages that may stay hidden. File labs are edited with a pull request instead.
"""

import re
from dataclasses import asdict, dataclass
import datetime as dt

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from infra_portal.auth import require_admin
from infra_portal.db import get_db
from infra_portal.labs import KIND_LABELS, file_slugs
from infra_portal.models import (
    VISIBILITIES,
    VISIBILITY_DRAFT,
    VISIBILITY_MEMBERS,
    VISIBILITY_PUBLIC,
    LabPage,
    Member,
)
from infra_portal.rendering import render_lab_markdown, slugify
from infra_portal.templating import templates

router = APIRouter(prefix="/admin/labs")

MAX_TITLE = 200
MAX_SLUG = 80
MAX_SUMMARY = 300
MAX_LINK = 500
# A long guide is a few hundred lines; this leaves plenty of room.
MAX_BODY = 200_000
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
# Plain words for the three states. A page starts as a draft.
VISIBILITY_CHOICES = {
    VISIBILITY_DRAFT: "Draft: only admins see it",
    VISIBILITY_MEMBERS: "Members only: any signed-in member (approved)",
    VISIBILITY_PUBLIC: "Public: everyone (finished)",
}
VISIBILITY_NAMES = {VISIBILITY_DRAFT: "Draft", VISIBILITY_MEMBERS: "Members only", VISIBILITY_PUBLIC: "Public"}
# The Write and Preview tabs submit these. Neither saves anything, as in the post editor.
VIEW_ACTIONS = ("write", "preview")


@dataclass
class Fields:
    """What the form holds, as typed, so a failed save or a tab switch keeps all of it."""

    title: str = ""
    slug: str = ""
    kind: str = "session"
    date: str = ""
    summary: str = ""
    link: str = ""
    body: str = ""
    visibility: str = VISIBILITY_DRAFT

    @classmethod
    def of(cls, page: LabPage) -> "Fields":
        return cls(
            title=page.title,
            slug=page.slug,
            kind=page.kind,
            date=page.date.isoformat(),
            summary=page.summary,
            link=page.link or "",
            body=page.body,
            visibility=page.visibility,
        )


def _form(
    title: str = Form(""),
    slug: str = Form(""),
    kind: str = Form(""),
    date: str = Form(""),
    summary: str = Form(""),
    link: str = Form(""),
    body: str = Form(""),
    visibility: str = Form(VISIBILITY_DRAFT),
) -> Fields:
    return Fields(
        title=title.strip(),
        slug=slug.strip().lower(),
        kind=kind,
        date=date.strip(),
        summary=summary.strip(),
        link=link.strip(),
        body=body,
        visibility=visibility,
    )


def _check(fields: Fields) -> tuple[str | None, dt.date | None]:
    """An error message for the form, or None, and the parsed date."""
    if not fields.title:
        return "A title is required.", None
    if len(fields.title) > MAX_TITLE:
        return f"Title must be at most {MAX_TITLE} characters.", None
    if fields.kind not in KIND_LABELS:
        return "Choose a kind.", None
    try:
        day = dt.date.fromisoformat(fields.date)
    except ValueError:
        return "Enter the date as YYYY-MM-DD.", None
    if not fields.summary:
        return "A summary is required.", None
    if len(fields.summary) > MAX_SUMMARY:
        return f"Summary must be at most {MAX_SUMMARY} characters.", None
    # The link goes in an href, and escaping does not stop a javascript: address.
    if fields.link and not re.match(r"^https?://", fields.link, re.IGNORECASE):
        return "The link must start with https:// or http://.", None
    if len(fields.link) > MAX_LINK:
        return f"The link must be at most {MAX_LINK} characters.", None
    if len(fields.body) > MAX_BODY:
        return f"The page must be at most {MAX_BODY:,} characters.", None
    if fields.visibility not in VISIBILITIES:
        return "Choose who can see the page.", None
    return None, day


def _check_slug(db: Session, slug: str) -> str | None:
    if not SLUG.match(slug) or len(slug) > MAX_SLUG:
        return (
            f"The address must be lowercase letters, digits and single hyphens, at most {MAX_SLUG} "
            "characters."
        )
    if slug in file_slugs():
        return f"A lab file in the repository already uses /labs/{slug}. Choose another address."
    if db.scalar(select(LabPage.id).where(LabPage.slug == slug)) is not None:
        return f"Another page already uses /labs/{slug}. Choose another address."
    return None


def _editor(
    request: Request,
    admin: Member,
    page: LabPage | None,
    fields: Fields,
    error: str | None = None,
    preview_html: str | None = None,
):
    return templates.TemplateResponse(
        request,
        "lab_editor.html",
        {
            "member": admin,
            "page": page,
            **asdict(fields),
            "error": error,
            "preview_html": preview_html,
            "kinds": KIND_LABELS,
            "visibilities": VISIBILITY_CHOICES,
        },
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT if error else status.HTTP_200_OK,
    )


def _view(request: Request, admin: Member, page: LabPage | None, fields: Fields, action: str):
    """Show the editor on the Write or Preview tab, keeping every field. Saves nothing."""
    if len(fields.body) > MAX_BODY:
        return _editor(request, admin, page, fields, f"The page must be at most {MAX_BODY:,} characters.")
    preview_html = render_lab_markdown(fields.body) if action == "preview" else None
    return _editor(request, admin, page, fields, None, preview_html)


def _get_page(db: Session, page_id: int) -> LabPage:
    page = db.get(LabPage, page_id)
    if page is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such page.")
    return page


@router.get("")
def list_pages(
    request: Request,
    admin: Member = Depends(require_admin),
    db: Session = Depends(get_db),
):
    pages = db.scalars(
        select(LabPage).options(selectinload(LabPage.edited_by)).order_by(LabPage.date.desc(), LabPage.slug)
    ).all()
    return templates.TemplateResponse(
        request,
        "admin_labs.html",
        {"member": admin, "pages": pages, "kinds": KIND_LABELS, "visibilities": VISIBILITY_NAMES},
    )


@router.get("/new")
def new_page_form(request: Request, admin: Member = Depends(require_admin)):
    return _editor(request, admin, None, Fields(date=dt.date.today().isoformat()))


@router.post("/new")
def create_page(
    request: Request,
    action: str = Form("save"),
    fields: Fields = Depends(_form),
    admin: Member = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if action in VIEW_ACTIONS:
        return _view(request, admin, None, fields, action)
    # Like a post, an empty address comes from the title. It is set once, here.
    if not fields.slug and fields.title:
        fields.slug = slugify(fields.title, max_length=MAX_SLUG)
    error, day = _check(fields)
    error = error or _check_slug(db, fields.slug)
    if error:
        return _editor(request, admin, None, fields, error)
    page = LabPage(
        slug=fields.slug,
        title=fields.title,
        kind=fields.kind,
        date=day,
        summary=fields.summary,
        link=fields.link or None,
        body=fields.body,
        visibility=fields.visibility,
        edited_by_id=admin.id,
    )
    db.add(page)
    db.commit()
    return RedirectResponse(f"/admin/labs/{page.id}/edit", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/{page_id}/edit")
def edit_page_form(
    page_id: int,
    request: Request,
    admin: Member = Depends(require_admin),
    db: Session = Depends(get_db),
):
    page = _get_page(db, page_id)
    return _editor(request, admin, page, Fields.of(page))


@router.post("/{page_id}/edit")
def update_page(
    page_id: int,
    request: Request,
    action: str = Form("save"),
    fields: Fields = Depends(_form),
    admin: Member = Depends(require_admin),
    db: Session = Depends(get_db),
):
    page = _get_page(db, page_id)
    # The address never changes after creation, so links to the page keep working.
    fields.slug = page.slug
    if action in VIEW_ACTIONS:
        return _view(request, admin, page, fields, action)
    error, day = _check(fields)
    if error:
        return _editor(request, admin, page, fields, error)
    page.title, page.kind, page.date = fields.title, fields.kind, day
    page.summary, page.link, page.body = fields.summary, fields.link or None, fields.body
    page.visibility = fields.visibility
    page.edited_by_id = admin.id
    db.commit()
    return RedirectResponse(f"/admin/labs/{page.id}/edit", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{page_id}/delete")
def delete_page(
    page_id: int,
    admin: Member = Depends(require_admin),
    db: Session = Depends(get_db),
):
    db.delete(_get_page(db, page_id))
    db.commit()
    return RedirectResponse("/admin/labs", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/preview")
def preview(body: str = Form(""), admin: Member = Depends(require_admin)) -> HTMLResponse:
    """The body as readers will see it, for the lab editor's Preview tab. Saves nothing."""
    if len(body) > MAX_BODY:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The page is too long.")
    return HTMLResponse(render_lab_markdown(body))

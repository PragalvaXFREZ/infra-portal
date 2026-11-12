"""Labs, experiments and learning sessions.

They come from two places and are shown as one list, newest first:

1. Markdown files in the content/labs folder. They ship in the public image, so they are always
   public. Adding one is a pull request that adds a file.
2. Rows in the `lab_pages` table, written on the admin pages (lab_admin.py). Each row has a
   visibility, so a page can stay hidden until it is ready. A hidden page cannot be a file,
   because the repository and the image are public.

Each file starts with a small header between two `---` lines, then the body in Markdown:

    ---
    title: CI, one step at a time
    kind: lab
    date: 2026-10-09
    link: https://github.com/...
    summary: One sentence for the list.
    ---
    Body in Markdown.

The file name, without `.md`, is the page address: `ci.md` is served at `/labs/ci`.
"""

from dataclasses import dataclass
from datetime import date
from functools import cache
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session, defer

from infra_portal.auth import current_member
from infra_portal.db import get_db
from infra_portal.models import (
    VISIBILITIES,
    VISIBILITY_DRAFT,
    VISIBILITY_MEMBERS,
    VISIBILITY_PUBLIC,
    LabPage,
    Member,
)
from infra_portal.rendering import render_lab_markdown
from infra_portal.templating import templates

LABS_DIR = Path(__file__).parent / "content" / "labs"
KIND_LABELS = {"lab": "Lab", "experiment": "Experiment", "session": "Learning session"}
KINDS = set(KIND_LABELS)
# Shown on cards and pages that not everyone can see. Public pages get no label.
VISIBILITY_LABELS = {VISIBILITY_DRAFT: "Draft", VISIBILITY_MEMBERS: "Members only"}
REQUIRED = ("title", "kind", "date", "summary")

router = APIRouter()


@dataclass(frozen=True)
class Lab:
    slug: str
    title: str
    kind: str
    date: date
    summary: str
    link: str | None
    body: str
    visibility: str = VISIBILITY_PUBLIC
    # The lab_pages row id for a database lab, None for a file lab.
    page_id: int | None = None

    @property
    def url(self) -> str:
        return f"/labs/{self.slug}"

    @property
    def kind_label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind.capitalize())

    @property
    def visibility_label(self) -> str | None:
        return VISIBILITY_LABELS.get(self.visibility)


def parse_lab(slug: str, text: str) -> Lab:
    """Split the header from the body and check the header. Raises ValueError on a bad file."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"{slug}: the file must start with a --- line")
    try:
        end = lines.index("---", 1)
    except ValueError:
        raise ValueError(f"{slug}: the header has no closing --- line") from None

    fields: dict[str, str] = {}
    for line in lines[1:end]:
        if not line.strip():
            continue
        key, sep, value = line.partition(":")
        if not sep:
            raise ValueError(f"{slug}: header line {line!r} has no colon")
        fields[key.strip()] = value.strip()

    missing = [key for key in REQUIRED if not fields.get(key)]
    if missing:
        raise ValueError(f"{slug}: missing {', '.join(missing)}")
    if fields["kind"] not in KINDS:
        raise ValueError(f"{slug}: kind must be one of {sorted(KINDS)}")

    return Lab(
        slug=slug,
        title=fields["title"],
        kind=fields["kind"],
        date=date.fromisoformat(fields["date"]),
        summary=fields["summary"],
        link=fields.get("link") or None,
        body="\n".join(lines[end + 1 :]).strip(),
    )


@cache
def all_labs() -> tuple[Lab, ...]:
    """Every lab, newest first. The files ship inside the image, so reading them once is enough."""
    labs = [parse_lab(path.stem, path.read_text(encoding="utf-8")) for path in LABS_DIR.glob("*.md")]
    return tuple(sorted(labs, key=lambda lab: (lab.date, lab.slug), reverse=True))


def file_slugs() -> set[str]:
    return {lab.slug for lab in all_labs()}


def can_see(visibility: str, member: Member | None) -> bool:
    """The one rule for who sees a lab. Every page that lists or shows labs goes through it."""
    if visibility == VISIBILITY_PUBLIC:
        return True
    if visibility == VISIBILITY_MEMBERS:
        return member is not None
    return member is not None and member.is_admin


def from_page(page: LabPage, with_body: bool = True) -> Lab:
    return Lab(
        slug=page.slug,
        title=page.title,
        kind=page.kind,
        date=page.date,
        summary=page.summary,
        link=page.link,
        body=page.body if with_body else "",
        visibility=page.visibility,
        page_id=page.id,
    )


def visible_labs(db: Session, member: Member | None, slug: str | None = None) -> list[Lab]:
    """File labs and the database labs this viewer may see, newest first. Only `slug` if given.

    Database rows are read on every request, not cached like the files, so a change on the
    admin pages shows at once. A row whose slug a file lab took later is left out: the file
    owns that address.
    """
    allowed = [visibility for visibility in VISIBILITIES if can_see(visibility, member)]
    query = select(LabPage).where(LabPage.visibility.in_(allowed))
    files = list(all_labs())
    if slug is None:
        # Lists show no body, so the long Markdown stays in the database.
        query = query.options(defer(LabPage.body, raiseload=True))
    else:
        query = query.where(LabPage.slug == slug)
        files = [lab for lab in files if lab.slug == slug]
    taken = file_slugs()
    pages = [from_page(page, with_body=slug is not None) for page in db.scalars(query)]
    labs = files + [lab for lab in pages if lab.slug not in taken]
    return sorted(labs, key=lambda lab: (lab.date, lab.slug), reverse=True)


@router.get("/labs")
def lab_list(
    request: Request,
    member: Member | None = Depends(current_member),
    db: Session = Depends(get_db),
):
    return templates.TemplateResponse(
        request, "labs.html", {"member": member, "labs": visible_labs(db, member)}
    )


@router.get("/labs/{slug}")
def lab_page(
    slug: str,
    request: Request,
    member: Member | None = Depends(current_member),
    db: Session = Depends(get_db),
):
    lab = next(iter(visible_labs(db, member, slug)), None)
    # The same 404 for "missing" and "hidden from you", so an address reveals nothing.
    if lab is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such lab.")
    return templates.TemplateResponse(
        request,
        "lab.html",
        {"member": member, "lab": lab, "body_html": render_lab_markdown(lab.body)},
    )

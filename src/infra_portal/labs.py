"""Labs and experiments, read from Markdown files in the content/labs folder.

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
Adding a lab is a pull request that adds a file. There is no database table.
"""

from dataclasses import dataclass
from datetime import date
from functools import cache
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status

from infra_portal.auth import current_member
from infra_portal.models import Member
from infra_portal.rendering import render_markdown
from infra_portal.templating import templates

LABS_DIR = Path(__file__).parent / "content" / "labs"
KINDS = {"lab", "experiment"}
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

    @property
    def url(self) -> str:
        return f"/labs/{self.slug}"


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


@router.get("/labs")
def lab_list(request: Request, member: Member | None = Depends(current_member)):
    return templates.TemplateResponse(request, "labs.html", {"member": member, "labs": all_labs()})


@router.get("/labs/{slug}")
def lab_page(slug: str, request: Request, member: Member | None = Depends(current_member)):
    lab = next((lab for lab in all_labs() if lab.slug == slug), None)
    if lab is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such lab.")
    return templates.TemplateResponse(
        request, "lab.html", {"member": member, "lab": lab, "body_html": render_markdown(lab.body)}
    )

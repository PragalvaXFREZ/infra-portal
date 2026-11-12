"""The learning path: the course as numbered stages, each with its steps in order.

A stage is a row in `course_stages`. Its steps are `lab_pages` rows that point at it: chunks
(a few videos, questions and a warm-up) and builds (the hands-on work that uses the chunks).
The path is for signed-in members. Who sees which step follows the same rule as for labs
(labs.can_see), applied to the step and to its stage, so a draft stays with the admins until
it is approved.

Path pages are served here, under /learn, and are left out of the lab lists.
"""

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Request, status
from markupsafe import Markup
from sqlalchemy import func, select
from sqlalchemy.orm import Session, defer

from infra_portal.auth import current_member
from infra_portal.db import get_db
from infra_portal.labs import KIND_LABELS, can_see
from infra_portal.models import VISIBILITY_DRAFT, CourseStage, LabPage, Member
from infra_portal.rendering import render_lab_markdown, render_markdown
from infra_portal.templating import templates

KIND_CHUNK = "chunk"
KIND_BUILD = "build"
# These two are counted inside their stage: "Chunk 3", "Build 1".
NUMBERED = {KIND_CHUNK: "Chunk", KIND_BUILD: "Build"}

router = APIRouter()


@dataclass(frozen=True)
class Step:
    """One page on the path, as one viewer sees it."""

    page: LabPage
    stage: CourseStage
    # "Chunk 3" or "Build 1". Counted over every page of the stage, hidden ones too, so a
    # hidden chunk 2 does not turn chunk 3 into "Chunk 2" for members.
    label: str
    # False for a page with no text yet. It is listed as coming and cannot be opened.
    written: bool

    @property
    def url(self) -> str:
        return f"/learn/{self.page.slug}"

    @property
    def is_build(self) -> bool:
        return self.page.kind == KIND_BUILD

    @property
    def is_draft(self) -> bool:
        return self.page.visibility == VISIBILITY_DRAFT


@dataclass(frozen=True)
class StageView:
    """One stage and the steps of it that one viewer may see."""

    stage: CourseStage
    steps: tuple[Step, ...]

    @property
    def written(self) -> tuple[Step, ...]:
        return tuple(step for step in self.steps if step.written)

    @property
    def state(self) -> str:
        """`open` when there is something to do, `planned` when nothing is written yet."""
        return "open" if self.written else "planned"

    @property
    def is_draft(self) -> bool:
        return self.stage.visibility == VISIBILITY_DRAFT

    @property
    def summary_html(self) -> Markup:
        """The stage's one or two sentences. Markdown, so a stage can link to labs that are files."""
        return render_markdown(self.stage.summary)


def load_path(db: Session, member: Member | None) -> list[StageView]:
    """Every stage this viewer may see, in order, each with the steps they may see.

    Nobody signed out sees any of it. The page text is not loaded: only whether there is any.
    """
    if member is None:
        return []
    stages = db.scalars(select(CourseStage).order_by(CourseStage.number)).all()
    rows = db.execute(
        select(LabPage, (func.length(LabPage.body) > 0).label("written"))
        .where(LabPage.stage_id.is_not(None))
        .options(defer(LabPage.body, raiseload=True))
        .order_by(LabPage.position, LabPage.id)
    ).all()

    views = []
    for stage in stages:
        counts: Counter[str] = Counter()
        steps = []
        for page, written in rows:
            if page.stage_id != stage.id:
                continue
            if page.kind in NUMBERED:
                counts[page.kind] += 1
                label = f"{NUMBERED[page.kind]} {counts[page.kind]}"
            else:
                label = KIND_LABELS.get(page.kind, page.kind.capitalize())
            if can_see(page.visibility, member):
                steps.append(Step(page=page, stage=stage, label=label, written=bool(written)))
        if can_see(stage.visibility, member):
            views.append(StageView(stage=stage, steps=tuple(steps)))
    return views


def all_steps(path: list[StageView]) -> list[Step]:
    return [step for view in path for step in view.steps]


def first_step(path: list[StageView]) -> Step | None:
    """Where to start: the first step that has text."""
    return next((step for step in all_steps(path) if step.written), None)


def builds(path: list[StageView]) -> list[Step]:
    """The written builds, for the labs page."""
    return [step for step in all_steps(path) if step.is_build and step.written]


def link_resolver(path: list[StageView]) -> Callable[[str], str | None]:
    """Turns a wikilink target from the notes into an address on this site, if there is one.

    The notes link to each other by file path, such as `course/stage-1-build-1`. The last part
    of that path is the page's address here. A link to a stage note goes to that stage on the
    path. Anything else, or anything this viewer may not see, stays plain text.
    """
    slugs = {step.page.slug for step in all_steps(path) if step.written}
    numbers = {view.stage.number for view in path}

    def resolve(target: str) -> str | None:
        name = target.partition("#")[0].strip().rstrip("/").rsplit("/", 1)[-1].removesuffix(".md")
        if name in slugs:
            return f"/learn/{name}"
        stage = re.match(r"stage-(\d+)(?:-|$)", name)
        if stage and int(stage.group(1)) in numbers:
            return f"/learn#stage-{int(stage.group(1))}"
        return None

    return resolve


@router.get("/learn")
def learning_path(
    request: Request,
    member: Member | None = Depends(current_member),
    db: Session = Depends(get_db),
):
    path = load_path(db, member)
    return templates.TemplateResponse(
        request, "learn.html", {"member": member, "path": path, "start": first_step(path)}
    )


@router.get("/learn/{slug}")
def learning_page(
    slug: str,
    request: Request,
    member: Member | None = Depends(current_member),
    db: Session = Depends(get_db),
):
    path = load_path(db, member)
    steps = all_steps(path)
    step = next((s for s in steps if s.page.slug == slug), None)
    # The same 404 for "missing", "hidden from you" and "you are signed out".
    if step is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such page.")
    body = db.scalar(select(LabPage.body).where(LabPage.id == step.page.id)) or ""
    # Earlier and later go only to steps that can be opened.
    openable = [s for s in steps if s.written or s is step]
    at = openable.index(step)
    return templates.TemplateResponse(
        request,
        "learn_page.html",
        {
            "member": member,
            "step": step,
            "body_html": render_lab_markdown(body, link_for=link_resolver(path)),
            "earlier": openable[at - 1] if at > 0 else None,
            "later": openable[at + 1] if at + 1 < len(openable) else None,
        },
    )

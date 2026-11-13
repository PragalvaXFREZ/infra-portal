"""Courses: what a member studies on the Learn page.

A course is a row in `course_stages`: the course notes call it a stage, and members meet each
stage as a course of its own, at /learn/<course>. Its steps are `lab_pages` rows that point at
it: chunks (a few videos, questions and a warm-up) and builds (the hands-on work that uses the
chunks, shown to members as labs). A step is served at /learn/<course>/<step>.

A course is split into sections. A section is a few chunks and the lab that uses them, so the
labs are easy to find and a member sees which lessons each one draws on.

Courses are for signed-in members. Who sees which step follows the same rule as for labs
(labs.can_see), applied to the step and to its course, so a draft stays with the admins until
it is approved. A course with nothing written in it is planned work, and only admins see it.

A member can tick a step as done and take the tick back. Ticks unlock nothing. They show the
member where they are, and the mentor where everyone is.
"""

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from markupsafe import Markup
from sqlalchemy import and_, delete, func, select
from sqlalchemy.orm import Session, defer

from infra_portal.auth import current_member, require_member
from infra_portal.db import get_db
from infra_portal.labs import KIND_LABELS, can_see
from infra_portal.models import (
    ROLE_MEMBER,
    VISIBILITY_DRAFT,
    CourseStage,
    LabPage,
    Member,
    PageProgress,
)
from infra_portal.rendering import render_lab_markdown, render_markdown
from infra_portal.templating import templates

KIND_CHUNK = "chunk"
KIND_BUILD = "build"
# These two are counted inside their course: "Chunk 3", "Build 1".
NUMBERED = {KIND_CHUNK: "Chunk", KIND_BUILD: "Build"}

router = APIRouter()


def video_time(minutes: int) -> str:
    """'23 min', '2 h', '4 h 11 min'."""
    hours, rest = divmod(minutes, 60)
    if not hours:
        return f"{rest} min"
    return f"{hours} h {rest} min" if rest else f"{hours} h"


@dataclass(frozen=True)
class Step:
    """One page of a course, as one viewer sees it."""

    page: LabPage
    stage: CourseStage
    # "Chunk 3" or "Build 1". The number is the step's own when it has one. Otherwise it is
    # counted over every page of the course, hidden ones too, so a hidden chunk 2 does not turn
    # the next chunk into "Chunk 2" for members.
    label: str
    # False for a page with no text yet. It is listed as coming and cannot be opened.
    written: bool
    # Which section of the course it is in, from 1. Counted over hidden pages too, like the label.
    section: int = 1
    # When this viewer ticked it, or None.
    done_at: datetime | None = None

    @property
    def done(self) -> bool:
        return self.done_at is not None

    @property
    def url(self) -> str:
        return f"{self.stage.url}/{self.page.slug}"

    @property
    def is_build(self) -> bool:
        return self.page.kind == KIND_BUILD

    @property
    def is_draft(self) -> bool:
        return self.page.visibility == VISIBILITY_DRAFT


@dataclass(frozen=True)
class Section:
    """A few lessons and the lab that uses them. The last section of a course may have no lab."""

    number: int
    lessons: tuple[Step, ...]
    lab: Step | None

    @property
    def minutes(self) -> int:
        return sum(step.page.minutes or 0 for step in self.lessons if step.written)

    @property
    def video(self) -> str:
        return video_time(self.minutes)


@dataclass(frozen=True)
class CourseView:
    """One course and the steps of it that one viewer may see."""

    stage: CourseStage
    steps: tuple[Step, ...]

    @property
    def url(self) -> str:
        return self.stage.url

    @property
    def written(self) -> tuple[Step, ...]:
        return tuple(step for step in self.steps if step.written)

    @property
    def listed(self) -> bool:
        """Whether there is anything to study. A course with nothing written is only a plan."""
        return bool(self.written)

    @property
    def lessons(self) -> tuple[Step, ...]:
        return tuple(step for step in self.steps if not step.is_build)

    @property
    def labs(self) -> tuple[Step, ...]:
        return tuple(step for step in self.steps if step.is_build)

    @property
    def sections(self) -> tuple[Section, ...]:
        numbers = sorted({step.section for step in self.steps})
        return tuple(
            Section(
                number=number,
                lessons=tuple(s for s in self.steps if s.section == number and not s.is_build),
                lab=next((s for s in self.steps if s.section == number and s.is_build), None),
            )
            for number in numbers
        )

    @property
    def minutes(self) -> int:
        return sum(step.page.minutes or 0 for step in self.written)

    @property
    def video(self) -> str:
        return video_time(self.minutes)

    @property
    def done_count(self) -> int:
        return sum(1 for step in self.written if step.done)

    @property
    def state(self) -> str:
        """`planned` when nothing is written yet, `done` when every written step is ticked."""
        if not self.written:
            return "planned"
        return "done" if self.done_count == len(self.written) else "open"

    @property
    def next(self) -> Step | None:
        """Where to go on in this course: the first step with text that is not ticked."""
        return next((step for step in self.written if not step.done), None)

    @property
    def is_draft(self) -> bool:
        return self.stage.visibility == VISIBILITY_DRAFT

    @property
    def summary_html(self) -> Markup:
        """The course's one or two sentences. Markdown, so it can link to labs that are files."""
        return render_markdown(self.stage.summary)


def load_courses(db: Session, member: Member | None, address: str | None = None) -> list[CourseView]:
    """Every course this viewer may see, in order, each with the steps they may see.

    With `address`, only that course, so a step page does not read the other courses' steps.
    Nobody signed out sees any of it. The page text is not loaded: only whether there is any.
    """
    if member is None:
        return []
    stages = db.scalars(select(CourseStage).order_by(CourseStage.number)).all()
    if address is not None:
        stages = [stage for stage in stages if stage.address == address]
    stages = [stage for stage in stages if can_see(stage.visibility, member)]
    if not stages:
        return []
    # One query for the steps and this member's ticks on them.
    rows = db.execute(
        select(LabPage, (func.length(LabPage.body) > 0).label("written"), PageProgress.done_at)
        .outerjoin(
            PageProgress,
            and_(PageProgress.page_id == LabPage.id, PageProgress.member_id == member.id),
        )
        .where(LabPage.stage_id.in_([stage.id for stage in stages]))
        .options(defer(LabPage.body, raiseload=True))
        .order_by(LabPage.position, LabPage.id)
    ).all()

    views = []
    for stage in stages:
        counts: Counter[str] = Counter()
        section = 1
        steps = []
        for page, written, done_at in rows:
            if page.stage_id != stage.id:
                continue
            if page.kind in NUMBERED:
                counts[page.kind] += 1
                number = counts[page.kind] if page.number is None else page.number
                label = f"{NUMBERED[page.kind]} {number}"
            else:
                label = KIND_LABELS.get(page.kind, page.kind.capitalize())
            if can_see(page.visibility, member):
                steps.append(
                    Step(page=page, stage=stage, label=label, written=bool(written), section=section, done_at=done_at)
                )
            # A build closes its section, whether or not this viewer sees it.
            if page.kind == KIND_BUILD:
                section += 1
        views.append(CourseView(stage=stage, steps=tuple(steps)))
    return views


def all_steps(courses: list[CourseView]) -> list[Step]:
    return [step for view in courses for step in view.steps]


def listed(courses: list[CourseView]) -> list[CourseView]:
    """The courses with something to study in them."""
    return [view for view in courses if view.listed]


def can_open(view: CourseView, member: Member | None) -> bool:
    """Whether this viewer may open the course's own pages. A course with nothing written is a
    plan, so it has no page for members, whatever its visibility says."""
    return view.listed or (member is not None and member.is_admin)


def planned(courses: list[CourseView]) -> list[CourseView]:
    """Courses with nothing written and nothing to say yet. Shown to admins only, as a short list."""
    return [view for view in courses if not view.listed and (view.is_draft or not view.stage.summary)]


def notes(courses: list[CourseView]) -> list[CourseView]:
    """Approved courses that are only a sentence, such as one that points at labs done elsewhere."""
    return [view for view in courses if not view.listed and not view.is_draft and view.stage.summary]


def notes_before(courses: list[CourseView], view: CourseView) -> list[CourseView]:
    """The sentence-only courses that sit just before this one: what a member does first."""
    only_a_sentence = notes(courses)
    before: list[CourseView] = []
    for other in courses:
        if other is view:
            return before
        if other.listed:
            before = []
        elif other in only_a_sentence:
            before.append(other)
    return []


def next_step(courses: list[CourseView]) -> Step | None:
    """Where to go on: the first step with text that this viewer has not ticked."""
    return next((view.next for view in courses if view.next), None)


def builds(courses: list[CourseView]) -> list[Step]:
    """The written builds, for the labs page."""
    return [step for step in all_steps(courses) if step.is_build and step.written]


@dataclass(frozen=True)
class MemberProgress:
    """How far one member is, for the mentor's overview."""

    member: Member
    done: int
    total: int
    # The first step they have not ticked, or None when they are through.
    next: Step | None
    last_tick: datetime | None


def everyone(db: Session) -> list[MemberProgress]:
    """Every member who has signed in, with their ticks on the steps a member can open.

    Drafts are left out of the count: a member cannot see them, so they are not behind on them.
    """
    # The courses as someone who is not an admin sees them. This Member is never saved.
    steps = [step for step in all_steps(load_courses(db, Member(role=ROLE_MEMBER))) if step.written]
    ticks: dict[int, dict[int, datetime]] = {}
    for member_id, page_id, done_at in db.execute(
        select(PageProgress.member_id, PageProgress.page_id, PageProgress.done_at)
    ):
        ticks.setdefault(member_id, {})[page_id] = done_at
    members = db.scalars(
        select(Member).where(Member.github_id.is_not(None)).order_by(Member.github_login)
    ).all()
    rows = []
    for member in members:
        mine = ticks.get(member.id, {})
        done = [mine[step.page.id] for step in steps if step.page.id in mine]
        rows.append(
            MemberProgress(
                member=member,
                done=len(done),
                total=len(steps),
                next=next((step for step in steps if step.page.id not in mine), None),
                last_tick=max(done, default=None),
            )
        )
    return rows


def link_resolver(
    view: CourseView, others: Callable[[], list[CourseView]]
) -> Callable[[str], str | None]:
    """Turns a wikilink target from the notes into an address on this site, if there is one.

    The notes link to each other by file path, such as `course/stage-1-build-1`. The last section
    of that path is the page's address here. A link to a stage note goes to that course.
    Anything else, or anything this viewer may not see, stays plain text.

    Most links stay inside the course the page is in, which is `view`. `others` reads the rest
    of the courses, and is called only for a link that points outside this one.
    """
    courses = [view]
    looked_further = False

    def find(name: str) -> str | None:
        for course in courses:
            for step in course.steps:
                if step.written and step.page.slug == name:
                    return step.url
        stage = re.match(r"stage-(\d+)(?:-|$)", name)
        if stage:
            # Only to a course with something in it: an empty one has no page for members.
            number = int(stage.group(1))
            return next((c.url for c in courses if c.stage.number == number and c.listed), None)
        return None

    def resolve(target: str) -> str | None:
        nonlocal courses, looked_further
        name = target.partition("#")[0].strip().rstrip("/").rsplit("/", 1)[-1].removesuffix(".md")
        found = find(name)
        if found is None and not looked_further:
            courses, looked_further = others(), True
            found = find(name)
        return found

    return resolve


def _not_found() -> HTTPException:
    # The same 404 for "missing", "hidden from you" and "you are signed out".
    return HTTPException(status.HTTP_404_NOT_FOUND, "No such page.")


def _course_page(request: Request, member: Member, courses: list[CourseView], view: CourseView):
    return templates.TemplateResponse(
        request,
        "course.html",
        {
            "member": member,
            "view": view,
            "before": notes_before(courses, view),
            # A way back to the list only when there is a list to go back to.
            "more": len(listed(courses)) > 1,
        },
    )


@router.get("/learn")
def learn(
    request: Request,
    member: Member | None = Depends(current_member),
    db: Session = Depends(get_db),
):
    courses = load_courses(db, member)
    shown = listed(courses)
    context = {
        "member": member,
        "courses": [view for view in courses if view.listed or view in notes(courses)],
        "listed": shown,
        "planned": planned(courses),
        "next": next_step(shown),
    }
    # With one course there is nothing to choose, so the page is that course.
    if len(shown) == 1:
        context |= {"view": shown[0], "before": notes_before(courses, shown[0]), "more": False}
    return templates.TemplateResponse(request, "learn.html", context)


@router.get("/learn/{address}")
def course_page(
    address: str,
    request: Request,
    member: Member | None = Depends(current_member),
    db: Session = Depends(get_db),
):
    courses = load_courses(db, member)
    mine = [view for view in courses if can_open(view, member)]
    view = next((v for v in mine if v.stage.address == address), None)
    if view is not None:
        return _course_page(request, member, courses, view)
    # Before courses had their own pages, a step was served at /learn/<step>.
    step = next((s for s in all_steps(mine) if s.page.slug == address), None)
    if step is None:
        raise _not_found()
    return RedirectResponse(step.url, status_code=status.HTTP_308_PERMANENT_REDIRECT)


def _step(db: Session, member: Member | None, address: str, slug: str) -> tuple[CourseView, Step]:
    """A step this viewer may see, and its course. Otherwise the usual 404."""
    view = next(iter(load_courses(db, member, address)), None)
    if view is None or not can_open(view, member):
        raise _not_found()
    step = next((s for s in view.steps if s.page.slug == slug), None)
    if step is None:
        raise _not_found()
    return view, step


@router.get("/learn/{address}/{slug}")
def step_page(
    address: str,
    slug: str,
    request: Request,
    member: Member | None = Depends(current_member),
    db: Session = Depends(get_db),
):
    view, step = _step(db, member, address, slug)
    body = db.scalar(select(LabPage.body).where(LabPage.id == step.page.id)) or ""
    # Earlier and later go only to steps that can be opened.
    openable = [s for s in view.steps if s.written or s is step]
    at = openable.index(step)
    return templates.TemplateResponse(
        request,
        "learn_page.html",
        {
            "member": member,
            "view": view,
            "step": step,
            "body_html": render_lab_markdown(
                body, link_for=link_resolver(view, others=lambda: load_courses(db, member))
            ),
            "earlier": openable[at - 1] if at > 0 else None,
            "later": openable[at + 1] if at + 1 < len(openable) else None,
        },
    )


def _own_step(db: Session, member: Member, address: str, slug: str) -> Step:
    """The step a member is ticking: one they may see and that has text. Otherwise the usual 404."""
    _, step = _step(db, member, address, slug)
    if not step.written:
        raise _not_found()
    return step


@router.post("/learn/{address}/{slug}/done")
def mark_done(
    address: str,
    slug: str,
    member: Member = Depends(require_member),
    db: Session = Depends(get_db),
):
    step = _own_step(db, member, address, slug)
    if not step.done:
        db.add(PageProgress(member_id=member.id, page_id=step.page.id))
        db.commit()
    return RedirectResponse(step.url, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/learn/{address}/{slug}/undo")
def mark_not_done(
    address: str,
    slug: str,
    member: Member = Depends(require_member),
    db: Session = Depends(get_db),
):
    step = _own_step(db, member, address, slug)
    db.execute(
        delete(PageProgress).where(PageProgress.member_id == member.id, PageProgress.page_id == step.page.id)
    )
    db.commit()
    return RedirectResponse(step.url, status_code=status.HTTP_303_SEE_OTHER)

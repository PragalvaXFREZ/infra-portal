"""Courses: what a member studies on the Learn page.

A course is a row in `course_stages`: the course notes call it a stage, and members meet each
stage as a course of its own, at /learn/<course>. Its steps are `lab_pages` rows that point at
it: chunks (a few videos, questions and a warm-up) and builds (the hands-on work that uses the
chunks, shown to members as labs). A step is served at /learn/<course>/<step>.

A course is split into sections. A section is a few chunks and the lab that uses them, so the
labs are easy to find and a member sees which lessons each one draws on.

Courses are for signed-in members. Who sees which step follows the same rule as for labs
(labs.can_see), applied to the step and to its course, so a draft stays with the admins until
it is approved. A course with nothing written in it is not listed for anyone. An admin reaches
it from the Course tab.

A member can tick a step as done and take the tick back. A lab is handed in with a link to
the work: a pull request or a write-up. The ticks show the member where they are, and the
mentor where everyone is (progress.py).

The courses are taken in order. A course is locked for a member until every step they can open
in the course before it is ticked (with_locks). A locked course is still shown, by name, with
what is left to do before it opens. Inside an open course every step is open.

Nobody confirms a tick. The mentor has two ways to step in, both on the member's page of the
Progress tab (progress.py): send a lab back with a note, which takes the tick away until the
member hands it in again, and open a course for one member who should not have to finish the
one before it.
"""

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from markupsafe import Markup
from sqlalchemy import and_, delete, func, select
from sqlalchemy.orm import Session, defer

from infra_portal.auth import current_member, require_member
from infra_portal.db import get_db
from infra_portal.labs import KIND_LABELS, can_see
from infra_portal.models import (
    VISIBILITY_DRAFT,
    CourseGrant,
    CourseStage,
    LabPage,
    LabReturn,
    Member,
    PageProgress,
)
from infra_portal.rendering import render_lab_markdown, render_markdown
from infra_portal.templating import templates

KIND_CHUNK = "chunk"
KIND_BUILD = "build"
# These two are counted inside their course: "Chunk 3", "Build 1".
NUMBERED = {KIND_CHUNK: "Chunk", KIND_BUILD: "Build"}
# The same length as the column it is stored in.
MAX_LINK = 500

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
    # The link they handed a lab in with, if any.
    link: str | None = None
    # The mentor's note, when this lab was sent back to them and is not handed in again yet.
    sent_back: LabReturn | None = None

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
    # The course to finish first, when this one is locked for the viewer (with_locks).
    locked_by: "CourseView | None" = None

    @property
    def url(self) -> str:
        return self.stage.url

    @property
    def locked(self) -> bool:
        return self.locked_by is not None

    @property
    def waiting_on(self) -> "tuple[CourseView, ...]":
        """The courses to finish before this one opens, first to last. Empty when it is open."""
        before: list[CourseView] = []
        view = self.locked_by
        while view is not None:
            before.insert(0, view)
            view = view.locked_by
        return tuple(before)

    @property
    def steps_to_go(self) -> int:
        """How many steps are left before this course opens."""
        return sum(len(view.written) - view.done_count for view in self.waiting_on)

    @property
    def focus(self) -> float:
        """From 0 to 1: how much of the way to opening this course is done. The picture on a
        locked course is drawn sharper as this grows."""
        total = sum(len(view.written) for view in self.waiting_on)
        return round(1 - self.steps_to_go / total, 3) if total else 1.0

    @property
    def written(self) -> tuple[Step, ...]:
        return tuple(step for step in self.steps if step.written)

    @property
    def listed(self) -> bool:
        """Whether there is anything to study. A course with nothing written is not listed."""
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
        """`empty` when nothing is written yet, `done` when every written step is ticked."""
        if not self.written:
            return "empty"
        return "done" if self.done_count == len(self.written) else "open"

    @property
    def next(self) -> Step | None:
        """Where to go on in this course: a lab the mentor sent back, if there is one, and
        otherwise the first step with text that is not ticked. None while the course is locked."""
        if self.locked:
            return None
        todo = [step for step in self.written if not step.done]
        return next((step for step in todo if step.sent_back), todo[0] if todo else None)

    @property
    def is_draft(self) -> bool:
        return self.stage.visibility == VISIBILITY_DRAFT

    @property
    def summary_html(self) -> Markup:
        """The course's one or two sentences. Markdown, so it can link to labs that are files."""
        return render_markdown(self.stage.summary)


def with_locks(
    courses: list[CourseView], member: Member, opened: frozenset[int] | set[int] = frozenset()
) -> list[CourseView]:
    """The same courses, each marked with the course that has to be finished first, if any.

    `courses` is every course the member may see, in order, with their ticks on the steps.
    A course is locked until every step the member can open in the course before it is ticked.
    Steps that are hidden or not written do not count, so nobody waits on those. A course with
    nothing to study in it is not part of the order.

    A course the member has ticked something in stays open, so a step added to an earlier
    course later on, or a lab sent back, does not shut them out of where they already are.
    `opened` is the ids of the stages a mentor opened for this member: those are open too.
    Admins are never locked.
    """
    if member.is_admin:
        return courses
    marked: list[CourseView] = []
    before: CourseView | None = None
    for view in courses:
        if view.listed:
            behind = before is not None and before.state != "done"
            let_in = view.done_count or view.stage.id in opened
            view = replace(view, locked_by=before if behind and not let_in else None)
            before = view
        marked.append(view)
    return marked


def load_courses(db: Session, member: Member | None) -> list[CourseView]:
    """Every course this viewer may see, in order, each with the steps they may see.

    Always all of them: whether a course is open depends on the courses before it.
    Nobody signed out sees any of it. The page text is not loaded: only whether there is any.
    """
    if member is None:
        return []
    stages = db.scalars(select(CourseStage).order_by(CourseStage.number)).all()
    stages = [stage for stage in stages if can_see(stage.visibility, member)]
    if not stages:
        return []
    # One query for the steps and this member's ticks on them.
    rows = db.execute(
        select(
            LabPage,
            (func.length(LabPage.body) > 0).label("written"),
            PageProgress.done_at,
            PageProgress.link,
            LabReturn,
        )
        .outerjoin(
            PageProgress,
            and_(PageProgress.page_id == LabPage.id, PageProgress.member_id == member.id),
        )
        .outerjoin(
            LabReturn,
            and_(LabReturn.page_id == LabPage.id, LabReturn.member_id == member.id),
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
        for page, written, done_at, link, sent_back in rows:
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
                    Step(
                        page=page,
                        stage=stage,
                        label=label,
                        written=bool(written),
                        section=section,
                        done_at=done_at,
                        link=link,
                        sent_back=sent_back,
                    )
                )
            # A build closes its section, whether or not this viewer sees it.
            if page.kind == KIND_BUILD:
                section += 1
        views.append(CourseView(stage=stage, steps=tuple(steps)))
    opened = set(db.scalars(select(CourseGrant.stage_id).where(CourseGrant.member_id == member.id)))
    return with_locks(views, member, opened)


def all_steps(courses: list[CourseView]) -> list[Step]:
    return [step for view in courses for step in view.steps]


def listed(courses: list[CourseView]) -> list[CourseView]:
    """The courses with something to study in them."""
    return [view for view in courses if view.listed]


def can_open(view: CourseView, member: Member | None) -> bool:
    """Whether this viewer may open the course's own pages. A course with nothing written has
    no page for members, whatever its visibility says."""
    return view.listed or (member is not None and member.is_admin)


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
    """Where to go on: a lab that was sent back comes first, wherever it is. Otherwise the
    first step with text that this viewer has not ticked."""
    steps = [view.next for view in courses if view.next]
    return next((step for step in steps if step.sent_back), steps[0] if steps else None)


def builds(courses: list[CourseView]) -> list[Step]:
    """The written builds of the courses that are open to the viewer, for the labs page."""
    return [step for view in courses if not view.locked for step in view.steps if step.is_build and step.written]


def following(courses: list[CourseView], view: CourseView) -> CourseView | None:
    """The course after this one, whether it is open yet or not."""
    after = [other for other in listed(courses) if other.stage.number > view.stage.number]
    return after[0] if after else None


def clean_link(text: str) -> str | None:
    """The link a lab is handed in with, or None when it is not a web address."""
    link = text.strip()
    if not link or len(link) > MAX_LINK or any(ch.isspace() or not ch.isprintable() for ch in link):
        return None
    try:
        parts = urlsplit(link)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or "." not in parts.netloc:
        return None
    return link


def link_resolver(courses: list[CourseView]) -> Callable[[str], str | None]:
    """Turns a wikilink target from the notes into an address on this site, if there is one.

    The notes link to each other by file path, such as `course/stage-1-build-1`. The last section
    of that path is the page's address here. A link to a stage note goes to that course.
    Anything else, or anything this viewer may not open, stays plain text: a hidden page, and a
    step of a course that is still locked for them.
    """

    def resolve(target: str) -> str | None:
        name = target.partition("#")[0].strip().rstrip("/").rsplit("/", 1)[-1].removesuffix(".md")
        for view in courses:
            if view.locked:
                continue
            for step in view.steps:
                if step.written and step.page.slug == name:
                    return step.url
        stage = re.match(r"stage-(\d+)(?:-|$)", name)
        if stage:
            # Only to a course with something in it: an empty one has no page for members.
            number = int(stage.group(1))
            return next((c.url for c in courses if c.stage.number == number and c.listed), None)
        return None

    return resolve


def _not_found() -> HTTPException:
    # The same 404 for "missing", "hidden from you" and "you are signed out".
    return HTTPException(status.HTTP_404_NOT_FOUND, "No such page.")


def _course_context(courses: list[CourseView], view: CourseView) -> dict:
    """What the course template needs besides the member."""
    return {
        "view": view,
        "before": notes_before(courses, view),
        # A way back to the list only when there is a list to go back to.
        "more": len(listed(courses)) > 1,
        # The course after this one, shown at the foot: locked, or open once this one is done.
        "after": following(courses, view),
    }


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
        "next": next_step(shown),
    }
    # With one course there is nothing to choose, so the page is that course.
    if len(shown) == 1:
        context |= _course_context(courses, shown[0])
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
        # A locked course has a page too: its name, and what is left before it opens.
        return templates.TemplateResponse(
            request, "course.html", {"member": member, **_course_context(courses, view)}
        )
    # Before courses had their own pages, a step was served at /learn/<step>.
    step = next((s for s in all_steps(mine) if s.page.slug == address), None)
    if step is None:
        raise _not_found()
    return RedirectResponse(step.url, status_code=status.HTTP_308_PERMANENT_REDIRECT)


def _step(
    db: Session, member: Member | None, address: str, slug: str
) -> tuple[list[CourseView], CourseView, Step]:
    """A step this viewer may see, with its course and all their courses. Otherwise the usual 404.

    The course may still be locked for them: the caller decides what that means.
    """
    courses = load_courses(db, member)
    view = next((v for v in courses if v.stage.address == address), None)
    if view is None or not can_open(view, member):
        raise _not_found()
    step = next((s for s in view.steps if s.page.slug == slug), None)
    if step is None:
        raise _not_found()
    return courses, view, step


def _step_page(
    request: Request,
    db: Session,
    member: Member,
    courses: list[CourseView],
    view: CourseView,
    step: Step,
    link: str = "",
    error: str | None = None,
):
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
            "body_html": render_lab_markdown(body, link_for=link_resolver(courses)),
            "earlier": openable[at - 1] if at > 0 else None,
            "later": openable[at + 1] if at + 1 < len(openable) else None,
            "after": following(courses, view),
            # What the member typed as the link to their work, and why it was not taken.
            "link": link,
            "error": error,
        },
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT if error else status.HTTP_200_OK,
    )


@router.get("/learn/{address}/{slug}")
def step_page(
    address: str,
    slug: str,
    request: Request,
    member: Member | None = Depends(current_member),
    db: Session = Depends(get_db),
):
    courses, view, step = _step(db, member, address, slug)
    if view.locked:
        # The course's own page says what is left before it opens.
        return RedirectResponse(view.url, status_code=status.HTTP_303_SEE_OTHER)
    # A lab that was sent back starts from the link it was handed in with.
    link = (step.sent_back.link or "") if step.sent_back else ""
    return _step_page(request, db, member, courses, view, step, link=link)


def _own_step(
    db: Session, member: Member, address: str, slug: str
) -> tuple[list[CourseView], CourseView, Step]:
    """The step a member is ticking: one they may see, that has text, in a course that is open
    to them. Otherwise the usual 404, or 403 while the course is locked."""
    courses, view, step = _step(db, member, address, slug)
    if not step.written:
        raise _not_found()
    if view.locked:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This course is still locked.")
    return courses, view, step


@router.post("/learn/{address}/{slug}/done")
def mark_done(
    address: str,
    slug: str,
    request: Request,
    link: str = Form(""),
    member: Member = Depends(require_member),
    db: Session = Depends(get_db),
):
    courses, view, step = _own_step(db, member, address, slug)
    if not step.done:
        work = None
        if step.is_build:
            # A lab is handed in with a link to the work. A lesson is only ticked.
            work = clean_link(link)
            if work is None:
                error = (
                    "Add a link to your work: a pull request, or the page where you wrote it up. "
                    "It has to start with https:// or http://."
                )
                return _step_page(request, db, member, courses, view, step, link=link.strip()[:MAX_LINK], error=error)
        db.add(PageProgress(member_id=member.id, page_id=step.page.id, link=work))
        # Handed in again: the mentor's note about the last try has done its work.
        db.execute(
            delete(LabReturn).where(LabReturn.member_id == member.id, LabReturn.page_id == step.page.id)
        )
        db.commit()
    return RedirectResponse(step.url, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/learn/{address}/{slug}/undo")
def mark_not_done(
    address: str,
    slug: str,
    member: Member = Depends(require_member),
    db: Session = Depends(get_db),
):
    _, _, step = _own_step(db, member, address, slug)
    db.execute(
        delete(PageProgress).where(PageProgress.member_id == member.id, PageProgress.page_id == step.page.id)
    )
    db.commit()
    return RedirectResponse(step.url, status_code=status.HTTP_303_SEE_OTHER)

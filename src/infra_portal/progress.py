"""Where each member is in the courses, for the mentor.

Everything here is read from the ticks members set themselves (`page_progress`): which steps,
and when. The site keeps no record of time spent or of pages opened, so nothing on these pages
claims to know either. "Video" is the length of the videos in the lessons a member ticked.

A member is counted on the steps a member can open. Drafts and unwritten steps are left out:
nobody is behind on something they cannot see. The steps of a course that is still locked for
them are counted, because they are what is left to do.

Nobody has to confirm a tick. The mentor steps in only when something looks wrong, from a
member's page: a lab can be sent back with a note, and a course can be opened for one member
who should not have to finish the one before it.
"""

from collections import Counter
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from infra_portal import course
from infra_portal.auth import require_admin
from infra_portal.course import CourseView, Step
from infra_portal.db import get_db
from infra_portal.models import (
    ROLE_MEMBER,
    CourseGrant,
    CourseStage,
    LabReturn,
    Member,
    PageProgress,
)
from infra_portal.templating import templates

router = APIRouter(prefix="/admin/progress")

# A member whose newest tick is older than this is shown as quiet.
QUIET_AFTER = timedelta(days=7)
# How far back the strip of days on a member's page goes.
STRIP_WEEKS = 8
# The note a lab is sent back with. The same length as the column it is stored in.
MAX_NOTE = 500


def _utc(moment: datetime) -> datetime:
    """The same moment in UTC. SQLite gives the time back without a zone; it was stored as UTC."""
    return moment.astimezone(UTC) if moment.tzinfo else moment.replace(tzinfo=UTC)


def days_ago(moment: datetime, now: datetime) -> str:
    """'today', 'yesterday', '12 days ago'. Counted in calendar days, in UTC."""
    days = (now.date() - _utc(moment).date()).days
    if days <= 0:
        return "today"
    return "yesterday" if days == 1 else f"{days} days ago"


@dataclass(frozen=True)
class Day:
    """One day on a member's strip of days, and the steps they ticked on it."""

    date: date
    ticks: int

    @property
    def level(self) -> int:
        """0 to 3, for how dark the day is drawn."""
        return min(self.ticks, 3)


@dataclass(frozen=True)
class MemberProgress:
    """One member's ticks, laid over the courses a member can open."""

    member: Member
    # The open courses, each step carrying this member's tick.
    courses: tuple[CourseView, ...]
    now: datetime
    # The courses a mentor opened for them, by stage id.
    grants: dict[int, CourseGrant]

    @property
    def steps(self) -> list[Step]:
        """Every step this member could tick."""
        return [step for view in self.courses for step in view.written]

    @property
    def total(self) -> int:
        return len(self.steps)

    @property
    def done(self) -> int:
        return sum(1 for step in self.steps if step.done)

    @property
    def next(self) -> Step | None:
        """The first step they have not ticked, or None when they are through."""
        return course.next_step(list(self.courses))

    @property
    def first_tick(self) -> datetime | None:
        return min((_utc(step.done_at) for step in self.steps if step.done), default=None)

    @property
    def last_tick(self) -> datetime | None:
        return max((_utc(step.done_at) for step in self.steps if step.done), default=None)

    @property
    def labs(self) -> list[Step]:
        return [step for step in self.steps if step.is_build]

    @property
    def labs_done(self) -> int:
        return sum(1 for step in self.labs if step.done)

    @property
    def minutes(self) -> int:
        """The length of the videos in the lessons they ticked."""
        return sum(step.page.minutes or 0 for step in self.steps if step.done)

    @property
    def total_minutes(self) -> int:
        return sum(step.page.minutes or 0 for step in self.steps)

    @property
    def video(self) -> str:
        return course.video_time(self.minutes)

    @property
    def total_video(self) -> str:
        return course.video_time(self.total_minutes)

    @property
    def state(self) -> str:
        """`idle` (nothing to do), `new` (no tick yet), `through`, `active` or `quiet`."""
        if not self.total:
            return "idle"
        if not self.done:
            return "new"
        if self.done == self.total:
            return "through"
        return "quiet" if self.now - self.last_tick > QUIET_AFTER else "active"

    @property
    def status(self) -> str:
        """The state in words, for the mentor."""
        return {
            "idle": "Nothing to do yet",
            "new": "Not started",
            "through": "Through everything",
            "active": "Active",
            "quiet": f"Quiet for {(self.now.date() - self.last_tick.date()).days} days" if self.last_tick else "",
        }[self.state]

    @property
    def last_tick_ago(self) -> str | None:
        return days_ago(self.last_tick, self.now) if self.last_tick else None

    @property
    def days(self) -> list[Day]:
        """The last few weeks, oldest first, starting on a Monday, with the ticks of each day."""
        ticked = Counter(_utc(step.done_at).date() for step in self.steps if step.done)
        today = self.now.date()
        start = today - timedelta(days=today.weekday(), weeks=STRIP_WEEKS - 1)
        return [
            Day(date=start + timedelta(days=n), ticks=ticked[start + timedelta(days=n)])
            for n in range((today - start).days + 1)
        ]


def roster(db: Session, now: datetime | None = None) -> list[MemberProgress]:
    """Every member who has signed in, with their ticks on the steps a member can open."""
    now = now or datetime.now(UTC)
    # The courses as someone who is not an admin sees them. This Member is never saved.
    open_courses = course.listed(course.load_courses(db, Member(role=ROLE_MEMBER)))
    ticks: dict[int, dict[int, tuple[datetime, str | None]]] = {}
    for member_id, page_id, done_at, link in db.execute(
        select(PageProgress.member_id, PageProgress.page_id, PageProgress.done_at, PageProgress.link)
    ):
        ticks.setdefault(member_id, {})[page_id] = (done_at, link)
    returns: dict[int, dict[int, LabReturn]] = {}
    for sent_back in db.scalars(select(LabReturn)):
        returns.setdefault(sent_back.member_id, {})[sent_back.page_id] = sent_back
    grants: dict[int, dict[int, CourseGrant]] = {}
    for grant in db.scalars(select(CourseGrant)):
        grants.setdefault(grant.member_id, {})[grant.stage_id] = grant
    members = db.scalars(select(Member).where(Member.github_id.is_not(None))).all()
    rows = []
    for member in sorted(members, key=lambda m: (m.name.lower(), m.github_login)):
        mine = ticks.get(member.id, {})
        back = returns.get(member.id, {})
        opened = grants.get(member.id, {})
        views = [
            CourseView(
                stage=view.stage,
                steps=tuple(
                    replace(step, done_at=done_at, link=link, sent_back=back.get(step.page.id))
                    for step in view.steps
                    for done_at, link in [mine.get(step.page.id, (None, None))]
                ),
            )
            for view in open_courses
        ]
        # Which courses are still locked depends on this member's own ticks, on what a mentor
        # opened for them, and on their role.
        locked = course.with_locks(views, member, set(opened))
        rows.append(MemberProgress(member=member, courses=tuple(locked), now=now, grants=opened))
    return rows


@dataclass(frozen=True)
class Line:
    """One member in one course: a row on the Progress tab."""

    member: Member
    # The course with this member's ticks on its steps.
    view: CourseView
    now: datetime

    @property
    def last_tick(self) -> datetime | None:
        return max((_utc(step.done_at) for step in self.view.written if step.done), default=None)

    @property
    def state(self) -> str:
        """`locked` (the course before is not finished), `new` (open, no tick yet), `done`,
        `active` or `quiet`."""
        if self.view.locked:
            return "locked"
        if not self.view.done_count:
            return "new"
        if self.view.state == "done":
            return "done"
        return "quiet" if self.now - self.last_tick > QUIET_AFTER else "active"

    @property
    def status(self) -> str:
        if self.state == "locked":
            return "Locked"
        if self.state == "new":
            return "Not started"
        if self.state == "done":
            return "Done"
        if self.state == "quiet":
            return f"Quiet for {(self.now.date() - self.last_tick.date()).days} days"
        return f"Active · {days_ago(self.last_tick, self.now)}"

    @property
    def detail(self) -> str:
        """The second, smaller line: what they open next, or when they finished."""
        if self.state == "done":
            return f"Finished {self.last_tick.strftime('%-d %b %Y')}"
        if self.state == "locked":
            return f"Opens after {self.view.locked_by.stage.title}"
        step = self.view.next
        # Before the first tick there is nothing to add, unless a lab came back.
        if step is None or (self.state == "new" and not step.sent_back):
            return ""
        back = " (sent back)" if step.sent_back else ""
        return f"Next: {'Lab · ' if step.is_build else ''}{step.label}{back}"


@dataclass(frozen=True)
class CourseRoster:
    """One course with every member's line through it, for the Progress tab."""

    view: CourseView
    lines: tuple[Line, ...]

    @property
    def started(self) -> int:
        return sum(1 for line in self.lines if line.view.done_count)

    @property
    def through(self) -> int:
        return sum(1 for line in self.lines if line.state == "done")

    @property
    def tally(self) -> list[tuple[Step, int]]:
        """Each step of the course with how many members have ticked it."""
        counts = Counter(step.page.id for line in self.lines for step in line.view.steps if step.done)
        return [(step, counts[step.page.id]) for step in self.view.steps]


def by_course(people: list[MemberProgress]) -> list[CourseRoster]:
    if not people:
        return []
    return [
        CourseRoster(
            view=view,
            lines=tuple(Line(member=p.member, view=p.courses[n], now=p.now) for p in people),
        )
        for n, view in enumerate(people[0].courses)
    ]


def started_counts(people: list[MemberProgress]) -> dict[int, int]:
    """Stage id -> how many members have ticked at least one of its steps."""
    return {group.view.stage.id: group.started for group in by_course(people)}


@router.get("")
def progress_page(
    request: Request,
    admin: Member = Depends(require_admin),
    db: Session = Depends(get_db),
):
    people = roster(db)
    states = Counter(person.state for person in people)
    return templates.TemplateResponse(
        request,
        "admin_progress.html",
        {
            "member": admin,
            "people": people,
            "courses": by_course(people),
            "states": states,
            "quiet_days": QUIET_AFTER.days,
        },
    )


@router.get("/{login}")
def member_page(
    login: str,
    request: Request,
    admin: Member = Depends(require_admin),
    db: Session = Depends(get_db),
):
    person = _person(db, login)
    return templates.TemplateResponse(
        request,
        "admin_member_progress.html",
        {"member": admin, "person": person, "weeks": STRIP_WEEKS},
    )


def _person(db: Session, login: str) -> MemberProgress:
    person = next((p for p in roster(db) if p.member.github_login == login.lower()), None)
    if person is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such member, or they have not signed in yet.")
    return person


def _to_member_page(person: MemberProgress, anchor: str = "") -> RedirectResponse:
    where = f"/admin/progress/{person.member.github_login}{anchor}"
    return RedirectResponse(where, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{login}/labs/{page_id}/send-back")
def send_lab_back(
    login: str,
    page_id: int,
    note: str = Form(""),
    admin: Member = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Take a member's tick off a lab, with a note they read on the lab. They hand it in again."""
    person = _person(db, login)
    step = next((s for s in person.steps if s.page.id == page_id and s.is_build and s.done), None)
    if step is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "They have not handed that lab in.")
    note = " ".join(note.split())
    if not note:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Say in a line what is missing.")
    if len(note) > MAX_NOTE:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Keep the note under {MAX_NOTE} characters.")
    member_id = person.member.id
    db.execute(delete(PageProgress).where(PageProgress.member_id == member_id, PageProgress.page_id == page_id))
    db.execute(delete(LabReturn).where(LabReturn.member_id == member_id, LabReturn.page_id == page_id))
    db.add(
        LabReturn(
            member_id=member_id,
            page_id=page_id,
            note=note,
            link=step.link,
            handed_in_at=step.done_at,
            returned_by_id=admin.id,
        )
    )
    db.commit()
    return _to_member_page(person, f"#step-{page_id}")


@router.post("/{login}/labs/{page_id}/take-back")
def take_note_back(
    login: str,
    page_id: int,
    admin: Member = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Sent back by mistake: the lab counts as handed in again, with the link and the day it had."""
    person = _person(db, login)
    member_id = person.member.id
    sent_back = db.get(LabReturn, (member_id, page_id))
    if sent_back is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That lab was not sent back.")
    if db.get(PageProgress, (member_id, page_id)) is None:
        tick = PageProgress(member_id=member_id, page_id=page_id, link=sent_back.link)
        if sent_back.handed_in_at is not None:
            tick.done_at = sent_back.handed_in_at
        db.add(tick)
    db.delete(sent_back)
    db.commit()
    return _to_member_page(person, f"#step-{page_id}")


def _stage_for(person: MemberProgress, stage_id: int) -> CourseStage:
    view = next((v for v in person.courses if v.stage.id == stage_id), None)
    if view is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such course, or members cannot see it.")
    return view.stage


@router.post("/{login}/courses/{stage_id}/open")
def open_course(
    login: str,
    stage_id: int,
    admin: Member = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Let one member into a course without finishing the one before it."""
    person = _person(db, login)
    stage = _stage_for(person, stage_id)
    if stage_id not in person.grants:
        db.add(CourseGrant(member_id=person.member.id, stage_id=stage_id, granted_by_id=admin.id))
        db.commit()
    return _to_member_page(person, f"#course-{stage.address}")


@router.post("/{login}/courses/{stage_id}/close")
def close_course(
    login: str,
    stage_id: int,
    admin: Member = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Take that back. The course locks again unless they have ticked something in it."""
    person = _person(db, login)
    stage = _stage_for(person, stage_id)
    db.execute(
        delete(CourseGrant).where(CourseGrant.member_id == person.member.id, CourseGrant.stage_id == stage_id)
    )
    db.commit()
    return _to_member_page(person, f"#course-{stage.address}")

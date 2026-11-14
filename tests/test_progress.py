"""The Progress tab: where each member is, read from the ticks they set."""

import datetime as dt
from datetime import UTC, datetime, timedelta

import pytest

from infra_portal import progress
from infra_portal.models import CourseStage, LabPage, Member, PageProgress

DAY = dt.date(2026, 11, 12)
# A Saturday.
NOW = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)


@pytest.fixture
def courses(session_factory):
    """Bread is open: two chunks, a lab, a hidden chunk and an unwritten lab. Pastry is open
    with one chunk. Cakes is a draft."""
    with session_factory() as db:
        bread = CourseStage(number=1, slug="bread", title="Bread", visibility="members")
        pastry = CourseStage(number=2, slug="pastry", title="Pastry", visibility="members")
        cakes = CourseStage(number=3, slug="cakes", title="Cakes", visibility="draft")
        db.add_all([bread, pastry, cakes])
        db.flush()
        for stage, slug, title, kind, position, visibility, body, minutes in (
            (bread, "stage-1-chunk-1", "Flour", "chunk", 1, "members", "Text.", 20),
            (bread, "stage-1-chunk-2", "Water", "chunk", 2, "members", "Text.", 25),
            (bread, "stage-1-build-1", "A loaf", "build", 3, "members", "Text.", None),
            (bread, "stage-1-chunk-3", "Hidden yeast", "chunk", 4, "draft", "Text.", 30),
            (bread, "stage-1-build-2", "A baguette", "build", 5, "members", "", None),
            (pastry, "stage-2-chunk-1", "Butter", "chunk", 1, "members", "Text.", 15),
            (cakes, "stage-3-chunk-1", "Sponges", "chunk", 1, "members", "Text.", 40),
        ):
            db.add(
                LabPage(
                    slug=slug,
                    title=title,
                    kind=kind,
                    date=DAY,
                    summary="A summary.",
                    body=body,
                    visibility=visibility,
                    stage_id=stage.id,
                    position=position,
                    minutes=minutes,
                )
            )
        db.commit()


@pytest.fixture
def people(courses, add_member, sign_in, session_factory):
    """Alice ticked both bread chunks, Bob ticked nothing, and the admin is signed in last."""
    add_member("alice", display_name="Alice")
    add_member("bob", display_name="Bob")
    add_member("invited-only")
    sign_in("bob", 43)
    sign_in("alice", 42)
    _tick(session_factory, "alice", "stage-1-chunk-1", NOW - timedelta(days=3))
    _tick(session_factory, "alice", "stage-1-chunk-2", NOW - timedelta(days=1))
    sign_in("boss", 1)


def _tick(session_factory, login, slug, when):
    with session_factory() as db:
        member = db.query(Member).filter_by(github_login=login).one()
        page = db.query(LabPage).filter_by(slug=slug).one()
        db.merge(PageProgress(member_id=member.id, page_id=page.id, done_at=when))
        db.commit()


def _roster(session_factory, now=NOW):
    with session_factory() as db:
        return {person.member.github_login: person for person in progress.roster(db, now)}


def _row(page, name, end='<li class="roster-row'):
    row = page[page.index(name) :]
    return row[: row.index(end)]


def test_a_member_is_counted_only_on_steps_a_member_can_open(people, session_factory):
    alice = _roster(session_factory)["alice"]
    # Not the hidden chunk, the unwritten lab or anything in the draft course.
    assert [step.page.title for step in alice.steps] == ["Flour", "Water", "A loaf", "Butter"]
    assert (alice.done, alice.total) == (2, 4)
    assert alice.next.page.title == "A loaf"
    assert (alice.labs_done, len(alice.labs)) == (0, 1)
    # The videos of the two lessons she ticked, out of the three open lessons.
    assert (alice.minutes, alice.total_minutes) == (45, 60)


def test_only_people_who_have_signed_in_are_listed(people, session_factory):
    assert sorted(_roster(session_factory)) == ["alice", "bob", "boss"]


def test_states_follow_the_ticks(people, session_factory):
    roster = _roster(session_factory)
    assert (roster["alice"].state, roster["alice"].status) == ("active", "Active")
    assert (roster["bob"].state, roster["bob"].status) == ("new", "Not started")
    # The same ticks, read three weeks later.
    later = _roster(session_factory, NOW + timedelta(days=20))["alice"]
    assert (later.state, later.status) == ("quiet", "Quiet for 21 days")
    for slug in ("stage-1-build-1", "stage-2-chunk-1"):
        _tick(session_factory, "alice", slug, NOW)
    assert _roster(session_factory)["alice"].state == "through"


def test_with_no_course_open_nobody_is_behind(add_member, sign_in, session_factory):
    sign_in("boss", 1)
    boss = _roster(session_factory)["boss"]
    assert (boss.state, boss.status, boss.total) == ("idle", "Nothing to do yet", 0)


def test_the_strip_of_days_starts_on_a_monday_and_ends_today(people, session_factory):
    days = _roster(session_factory)["alice"].days
    assert days[0].date.weekday() == 0 and days[-1].date == NOW.date()
    assert len(days) == 7 * (progress.STRIP_WEEKS - 1) + 6
    assert {day.date: day.ticks for day in days if day.ticks} == {dt.date(2026, 11, 11): 1, dt.date(2026, 11, 13): 1}


def test_progress_tab_draws_each_member_in_each_course(client, people):
    page = client.get("/admin/progress").text
    bread = page[page.index('id="course-bread"') : page.index('id="course-pastry"')]
    pastry = page[page.index('id="course-pastry"') :]
    assert 'id="course-cakes"' not in page and "Hidden yeast" not in page
    assert "1 of 3 started" in bread and "0 of 3 started" in pastry
    alice = _row(bread, "Alice")
    assert 'href="/admin/progress/alice"' in bread
    assert "2 of 3" in alice and "Active" in alice and "Next: Lab · Build 1" in alice
    # Her line: two lessons ticked, the lab not, and the unwritten lab drawn as coming.
    assert alice.count('class="is-done"') == 2 and "A baguette (not written yet)" in alice
    assert "Not started" in _row(bread, "Bob")
    # Pastry opens once Bread is finished.
    assert "Locked" in _row(pastry, "Alice") and "Opens after Bread" in _row(pastry, "Alice")
    # Under the lines, how many have ticked each step.
    tally = bread[bread.index('class="track tally"') :]
    assert [cell.split(">")[1].split("<")[0] for cell in tally.split('<span title="')[1:]] == ["1", "1", "0", ""]


def test_progress_tab_counts_people_by_state(client, people):
    page = client.get("/admin/progress").text
    pulse = page[page.index('<ul class="pulse">') : page.index("</ul>", page.index('<ul class="pulse">'))]
    assert "<strong>1</strong> active" in pulse and "<strong>2</strong> not started" in pulse


def test_a_finished_course_says_when(client, people, session_factory):
    _tick(session_factory, "alice", "stage-1-build-1", datetime(2026, 11, 14, 9, 0, tzinfo=UTC))
    bread = client.get("/admin/progress").text
    bread = bread[bread.index('id="course-bread"') : bread.index('id="course-pastry"')]
    alice = _row(bread, "Alice")
    assert "Done" in alice and "Finished 14 Nov 2026" in alice and "1 through" in bread


def test_member_page_lists_every_step_with_its_tick(client, people):
    page = client.get("/admin/progress/alice")
    assert page.status_code == 200
    text = page.text
    assert "Alice</h1>" in text and "<strong>2</strong>" in text and "of 4 steps ticked" in text
    assert "45 min" in text and "1 h in the open lessons" in text
    flour = _row(text, ">Flour<", "</tr>")
    assert "11 Nov 2026" in flour
    assert "Here now" in _row(text, ">A loaf<", "</tr>")
    assert "Not written yet" in _row(text, "A baguette</td>", "</tr>")
    assert "Not yet" in _row(text, ">Butter<", "</tr>")
    assert "Hidden yeast" not in text and "Sponges" not in text
    assert "Locked until Bread is finished" in text


def test_member_page_links_to_the_work_a_lab_was_handed_in_with(client, people, session_factory):
    with session_factory() as db:
        alice = db.query(Member).filter_by(github_login="alice").one()
        loaf = db.query(LabPage).filter_by(slug="stage-1-build-1").one()
        db.add(PageProgress(member_id=alice.id, page_id=loaf.id, done_at=NOW, link="https://example.com/alice/loaf/pull/3"))
        db.commit()
    row = _row(client.get("/admin/progress/alice").text, ">A loaf<", "</tr>")
    assert '<a href="https://example.com/alice/loaf/pull/3" rel="noopener nofollow">Their work</a>' in row
    assert "<strong>1</strong>" in client.get("/admin/progress/alice").text


def test_member_page_is_only_for_people_who_have_signed_in(client, people):
    assert client.get("/admin/progress/invited-only").status_code == 404
    assert client.get("/admin/progress/nobody").status_code == 404
    # The address is not case sensitive, like a GitHub login.
    assert client.get("/admin/progress/Alice").status_code == 200


def test_overview_and_members_tab_lead_to_a_members_page(client, people):
    overview = client.get("/admin").text
    assert 'href="/admin/progress/alice"' in overview and 'href="/admin/progress"' in overview
    members = client.get("/admin/members").text
    assert 'href="/admin/progress/alice"' in members and 'href="/admin/progress/invited-only"' not in members


def test_course_tab_says_how_many_have_started_an_open_stage(client, people):
    page = client.get("/admin/course").text
    assert '<a href="/admin/progress#course-bread">1 of 3 started</a>' in page
    # A draft stage has nothing for members to tick.
    assert "#course-cakes" not in page


def test_a_tick_is_put_on_its_utc_day_whatever_zone_it_comes_back_in():
    # 01:30 in Kathmandu on the 14th is still the 13th in UTC.
    kathmandu = dt.timezone(timedelta(hours=5, minutes=45))
    late = datetime(2026, 11, 14, 1, 30, tzinfo=kathmandu)
    assert progress.days_ago(late, NOW) == "yesterday"
    assert progress.days_ago(datetime(2026, 11, 14, 1, 30), NOW) == "today"

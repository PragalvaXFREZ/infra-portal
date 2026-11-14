"""Courses are taken in order, and a lab is handed in with a link to the work.

Nobody confirms a tick. The mentor can send a lab back with a note, and open a course for one
member who should not have to finish the one before it.
"""

import datetime as dt

import pytest
from sqlalchemy import text

from infra_portal import course
from infra_portal.models import CourseGrant, CourseStage, LabPage, LabReturn, Member, PageProgress

DAY = dt.date(2026, 11, 12)
WORK = {"link": "https://example.com/alice/loaf/pull/3"}
BREAD = ("stage-1-chunk-1", "stage-1-chunk-2", "stage-1-build-1")


def _page(db, stage, slug, title, kind, position, visibility="members", body="Text."):
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
        )
    )


@pytest.fixture
def courses(session_factory):
    """Three open courses in order. Bread has two chunks and a lab, and also a hidden chunk
    and an unwritten lab, which nobody has to wait for. Pastry and Cakes have one chunk each,
    and Pastry has a lab."""
    with session_factory() as db:
        bread = CourseStage(number=1, slug="bread", title="Bread", visibility="members")
        pastry = CourseStage(number=2, slug="pastry", title="Pastry", visibility="members")
        cakes = CourseStage(number=3, slug="cakes", title="Cakes", visibility="members")
        db.add_all([bread, pastry, cakes])
        db.flush()
        _page(db, bread, "stage-1-chunk-1", "Flour", "chunk", 1)
        _page(db, bread, "stage-1-chunk-2", "Water", "chunk", 2)
        _page(db, bread, "stage-1-build-1", "A loaf", "build", 3)
        _page(db, bread, "stage-1-chunk-3", "Hidden yeast", "chunk", 4, visibility="draft")
        _page(db, bread, "stage-1-build-2", "A baguette", "build", 5, body="")
        _page(db, pastry, "stage-2-chunk-1", "Butter", "chunk", 1, body="See [[notes/stage-3-chunk-1|sponges]] and [[notes/stage-1-chunk-1|flour]].")
        _page(db, pastry, "stage-2-build-1", "A tart", "build", 2)
        _page(db, cakes, "stage-3-chunk-1", "Sponges", "chunk", 1)
        db.commit()


@pytest.fixture
def alice(courses, add_member, sign_in):
    add_member("alice")
    sign_in("alice", 42)


def _finish_bread(client):
    for slug in BREAD:
        assert client.post(f"/learn/bread/{slug}/done", data=WORK, follow_redirects=False).status_code == 303


def _ticks(session_factory):
    with session_factory() as db:
        return {page.slug: tick.link for tick, page in db.query(PageProgress, LabPage).join(LabPage, LabPage.id == PageProgress.page_id)}


def test_the_next_course_is_listed_behind_a_lock(client, alice):
    page = client.get("/learn").text
    pastry = page[page.index(">Pastry</a>") : page.index(">Cakes</a>")]
    assert "Locked · 3 steps to go · opens after Bread" in pastry and 'class="track"' not in pastry
    # The course after that waits on the one before it, and counts the steps of both.
    assert "Locked · 5 steps to go · opens after Pastry" in page[page.index(">Cakes</a>") :]
    # The first course is open as before.
    first = page[page.index(">Bread</a>") : page.index(">Pastry</a>")]
    assert "Not started" in first and 'class="track"' in first and "Locked" not in first


def test_a_locked_course_has_a_page_that_says_what_is_left(client, alice):
    client.post("/learn/bread/stage-1-chunk-1/done")
    page = client.get("/learn/pastry")
    assert page.status_code == 200
    assert "Pastry</h1>" in page.text and "Opens when you finish Bread" in page.text
    assert "<strong>2</strong> steps to go in Bread" in page.text
    assert 'style="--focus: 0.333"' in page.text
    # The way on is the next step of the course that is open.
    assert '<a class="continue-go" href="/learn/bread/stage-1-chunk-2">Continue Bread</a>' in page.text
    # The names of its steps are on the page, out of focus. None of them is a link.
    assert "Butter" in page.text and "/learn/pastry/" not in page.text
    # Two courses on: what is left is counted over both.
    cakes = client.get("/learn/cakes").text
    assert "Opens when you finish Pastry" in cakes and "<strong>4</strong> steps to go in Bread, then Pastry" in cakes
    assert '<a class="continue-go" href="/learn/bread/stage-1-chunk-2">Continue Bread</a>' in cakes


def test_a_step_of_a_locked_course_cannot_be_opened_or_ticked(client, alice, session_factory):
    page = client.get("/learn/pastry/stage-2-chunk-1", follow_redirects=False)
    assert page.status_code == 303 and page.headers["location"] == "/learn/pastry"
    # Its old addresses end at the same place.
    assert "Opens when you finish Bread" in client.get("/learn/stage-2-chunk-1").text
    assert "Opens when you finish Bread" in client.get("/labs/stage-2-build-1").text
    assert client.post("/learn/pastry/stage-2-chunk-1/done").status_code == 403
    assert client.post("/learn/pastry/stage-2-build-1/done", data=WORK).status_code == 403
    assert client.post("/learn/pastry/stage-2-chunk-1/undo").status_code == 403
    assert _ticks(session_factory) == {}


def test_finishing_a_course_opens_the_next_one_only(client, alice):
    _finish_bread(client)
    assert client.get("/learn/pastry/stage-2-chunk-1").status_code == 200
    assert client.get("/learn/cakes/stage-3-chunk-1", follow_redirects=False).status_code == 303
    page = client.get("/learn").text
    assert "Locked" not in page[page.index(">Pastry</a>") : page.index(">Cakes</a>")]
    assert "Locked · 2 steps to go · opens after Pastry" in page
    # The finished course points at the one that has just opened, and its last step does too.
    bread = client.get("/learn/bread").text
    assert "Open now" in bread and "Next course: Pastry" in bread
    assert '<a class="continue-go" href="/learn/pastry/stage-2-chunk-1">Start Pastry</a>' in bread
    assert "Next course, open now" in client.get("/learn/bread/stage-1-build-1").text


def test_an_open_course_shows_the_next_one_behind_its_door(client, alice):
    page = client.get("/learn/bread").text
    after = page[page.index('<div class="course-after">') :]
    assert "Next course: Pastry" in after and "<strong>3</strong> steps to go in Bread" in after
    # The last course has nothing after it.
    assert "course-after" not in client.get("/learn/cakes").text


def test_taking_a_tick_back_locks_a_course_that_was_not_started(client, alice):
    _finish_bread(client)
    client.post("/learn/bread/stage-1-chunk-1/undo")
    assert client.get("/learn/pastry/stage-2-chunk-1", follow_redirects=False).status_code == 303


def test_a_started_course_stays_open_when_an_earlier_one_gets_a_new_step(client, alice, session_factory):
    _finish_bread(client)
    client.post("/learn/pastry/stage-2-chunk-1/done")
    with session_factory() as db:
        bread = db.query(CourseStage).filter_by(slug="bread").one()
        _page(db, bread, "stage-1-chunk-4", "Salt", "chunk", 6)
        db.commit()
    assert client.get("/learn/pastry/stage-2-build-1").status_code == 200
    assert "Opens when you finish" not in client.get("/learn/pastry").text
    # The new step is still where they are sent next.
    home = client.get("/").text
    # It is chunk 4: the hidden chunk keeps its number.
    assert "Chunk 4: Salt" in home[home.index('<aside class="hero-card">') : home.index("</aside>")]
    # Cakes was never started, so it waits.
    assert client.get("/learn/cakes/stage-3-chunk-1", follow_redirects=False).status_code == 303


def test_an_admin_is_never_locked(client, courses, sign_in):
    sign_in("boss", 1)
    assert client.get("/learn/cakes/stage-3-chunk-1").status_code == 200
    assert "Locked" not in client.get("/learn").text


def test_a_link_into_a_locked_course_stays_plain_text(client, alice):
    _finish_bread(client)
    text = client.get("/learn/pastry/stage-2-chunk-1").text
    assert "sponges" in text and "/learn/cakes/stage-3-chunk-1" not in text
    assert '<a href="/learn/bread/stage-1-chunk-1">flour</a>' in text


def test_the_labs_page_lists_only_the_labs_of_open_courses(client, alice):
    page = client.get("/labs").text
    assert "A loaf" in page and "A tart" not in page
    _finish_bread(client)
    assert "A tart" in client.get("/labs").text


def test_a_lab_is_handed_in_with_a_link(client, alice, session_factory):
    page = client.get("/learn/bread/stage-1-build-1").text
    assert 'name="link"' in page and "Hand in" in page and "Mark as done" not in page
    done = client.post("/learn/bread/stage-1-build-1/done", data=WORK, follow_redirects=False)
    assert done.status_code == 303
    assert _ticks(session_factory) == {"stage-1-build-1": WORK["link"]}
    page = client.get("/learn/bread/stage-1-build-1").text
    assert "Handed in on" in page
    assert f'<a href="{WORK["link"]}" rel="noopener nofollow">your work</a>' in page
    # Taken back, the link goes with the tick.
    client.post("/learn/bread/stage-1-build-1/undo")
    assert _ticks(session_factory) == {}


@pytest.mark.parametrize(
    "link",
    ["", "   ", "my-repo", "javascript:alert(1)", "ftp://example.com/x", "https://nodot/x", "https://example.com/a b", "https://example.com/" + "x" * 500],
)
def test_a_lab_without_a_real_link_is_not_handed_in(client, alice, session_factory, link):
    page = client.post("/learn/bread/stage-1-build-1/done", data={"link": link})
    assert page.status_code == 422 and "Add a link to your work" in page.text
    # The page is the lab again, with what was typed still in the field.
    assert "A loaf</h1>" in page.text
    assert _ticks(session_factory) == {}


def test_what_was_typed_as_a_link_is_escaped(client, alice):
    page = client.post("/learn/bread/stage-1-build-1/done", data={"link": '"><script>alert(1)</script>'})
    assert page.status_code == 422 and "<script>alert(1)" not in page.text


def test_a_lesson_is_ticked_without_a_link(client, alice, session_factory):
    assert 'name="link"' not in client.get("/learn/bread/stage-1-chunk-1").text
    # A link sent with a lesson is not kept.
    client.post("/learn/bread/stage-1-chunk-1/done", data=WORK)
    assert _ticks(session_factory) == {"stage-1-chunk-1": None}


def test_a_lab_ticked_before_links_were_asked_for_still_counts(client, alice, session_factory):
    with session_factory() as db:
        for slug in BREAD:
            page = db.query(LabPage).filter_by(slug=slug).one()
            db.add(PageProgress(member_id=1, page_id=page.id))
        db.commit()
    assert client.get("/learn/pastry/stage-2-chunk-1").status_code == 200
    assert "Handed in on" in client.get("/learn/bread/stage-1-build-1").text


def test_clean_link_keeps_a_web_address_and_nothing_else():
    assert course.clean_link("  https://github.com/a/b/pull/1 ") == "https://github.com/a/b/pull/1"
    assert course.clean_link("http://example.com") == "http://example.com"
    assert course.clean_link("https://[bad") is None
    assert course.clean_link("data:text/html,x") is None


# ---------- the mentor's two ways to step in ----------

NOTE = {"note": "The link opens an empty repository."}


def _id(session_factory, model, **where):
    with session_factory() as db:
        return db.query(model).filter_by(**where).one().id


@pytest.fixture
def loaf(session_factory, alice):
    return _id(session_factory, LabPage, slug="stage-1-build-1")


@pytest.fixture
def pastry(session_factory, alice):
    return _id(session_factory, CourseStage, slug="pastry")


def _returns(session_factory):
    with session_factory() as db:
        return [(r.note, r.link) for r in db.query(LabReturn)]


def _as_boss(sign_in):
    sign_in("boss", 1)


def _as_alice(sign_in):
    sign_in("alice", 42)


def test_the_mentor_sends_a_lab_back_and_the_member_hands_it_in_again(client, sign_in, session_factory, loaf):
    client.post("/learn/bread/stage-1-build-1/done", data=WORK)
    _as_boss(sign_in)
    page = client.get("/admin/progress/alice").text
    assert f'action="/admin/progress/alice/labs/{loaf}/send-back"' in page
    sent = client.post(f"/admin/progress/alice/labs/{loaf}/send-back", data=NOTE, follow_redirects=False)
    assert sent.status_code == 303 and sent.headers["location"] == f"/admin/progress/alice#step-{loaf}"
    # The tick is gone, and the note is kept with the link that was handed in.
    assert _ticks(session_factory) == {}
    assert _returns(session_factory) == [(NOTE["note"], WORK["link"])]
    page = client.get("/admin/progress/alice").text
    assert "Sent back" in page and "The link opens an empty repository." in page
    assert f'action="/admin/progress/alice/labs/{loaf}/take-back"' in page
    # The lab that came back is where she is sent next, ahead of the lessons she has not ticked.
    assert "Next: Lab · Build 1 (sent back)" in client.get("/admin/progress").text

    _as_alice(sign_in)
    lab = client.get("/learn/bread/stage-1-build-1").text
    assert "Sent back on" in lab and "The link opens an empty repository." in lab
    # The form starts from the link she handed in.
    assert f'value="{WORK["link"]}"' in lab and "Hand in again" in lab
    assert "Sent back with a note" in client.get("/learn/bread").text
    home = client.get("/").text
    assert "Build 1: A loaf" in home[home.index('<aside class="hero-card">') : home.index("</aside>")]
    client.post("/learn/bread/stage-1-build-1/done", data={"link": "https://example.com/alice/loaf/pull/4"})
    assert _ticks(session_factory) == {"stage-1-build-1": "https://example.com/alice/loaf/pull/4"}
    assert _returns(session_factory) == []
    assert "Sent back" not in client.get("/learn/bread/stage-1-build-1").text


def test_a_lab_sent_back_by_mistake_counts_again_with_its_link(client, sign_in, session_factory, loaf):
    client.post("/learn/bread/stage-1-build-1/done", data=WORK)
    _as_boss(sign_in)
    with session_factory() as db:
        handed_in = db.query(PageProgress).one().done_at
        db.query(PageProgress).update({"done_at": handed_in - dt.timedelta(days=9)})
        db.commit()
    client.post(f"/admin/progress/alice/labs/{loaf}/send-back", data=NOTE)
    back = client.post(f"/admin/progress/alice/labs/{loaf}/take-back", follow_redirects=False)
    assert back.status_code == 303
    # It keeps the day it was handed in, not the day of the mistake.
    with session_factory() as db:
        assert db.query(PageProgress).one().done_at == handed_in - dt.timedelta(days=9)
    assert _ticks(session_factory) == {"stage-1-build-1": WORK["link"]} and _returns(session_factory) == []
    assert client.post(f"/admin/progress/alice/labs/{loaf}/take-back").status_code == 404


def test_only_a_lab_that_was_handed_in_can_be_sent_back(client, sign_in, session_factory, loaf):
    client.post("/learn/bread/stage-1-chunk-1/done")
    flour = _id(session_factory, LabPage, slug="stage-1-chunk-1")
    _as_boss(sign_in)
    # Not handed in yet, a lesson, a page that does not exist, a member who does not exist.
    for path in (f"alice/labs/{loaf}", f"alice/labs/{flour}", "alice/labs/999", f"nobody/labs/{loaf}"):
        assert client.post(f"/admin/progress/{path}/send-back", data=NOTE).status_code == 404, path
    assert _ticks(session_factory) == {"stage-1-chunk-1": None} and _returns(session_factory) == []


@pytest.mark.parametrize("note", ["", "   ", "x" * 501])
def test_a_lab_goes_back_only_with_a_short_note(client, sign_in, session_factory, loaf, note):
    client.post("/learn/bread/stage-1-build-1/done", data=WORK)
    _as_boss(sign_in)
    assert client.post(f"/admin/progress/alice/labs/{loaf}/send-back", data={"note": note}).status_code == 422
    assert _ticks(session_factory) == {"stage-1-build-1": WORK["link"]}


def test_the_note_is_escaped_where_it_is_shown(client, sign_in, loaf):
    client.post("/learn/bread/stage-1-build-1/done", data=WORK)
    _as_boss(sign_in)
    client.post(f"/admin/progress/alice/labs/{loaf}/send-back", data={"note": "<script>alert(1)</script>"})
    assert "<script>alert(1)" not in client.get("/admin/progress/alice").text
    _as_alice(sign_in)
    assert "<script>alert(1)" not in client.get("/learn/bread/stage-1-build-1").text


def test_a_lab_sent_back_locks_the_next_course_unless_it_was_started(client, sign_in, loaf):
    _finish_bread(client)
    _as_boss(sign_in)
    client.post(f"/admin/progress/alice/labs/{loaf}/send-back", data=NOTE)
    _as_alice(sign_in)
    assert client.get("/learn/pastry/stage-2-chunk-1", follow_redirects=False).status_code == 303
    # Handed in again and started on Pastry: a second note does not shut her out of it.
    client.post("/learn/bread/stage-1-build-1/done", data=WORK)
    client.post("/learn/pastry/stage-2-chunk-1/done")
    _as_boss(sign_in)
    client.post(f"/admin/progress/alice/labs/{loaf}/send-back", data=NOTE)
    assert "Next: Lab · Build 1 (sent back)" in client.get("/admin/progress").text
    _as_alice(sign_in)
    assert client.get("/learn/pastry/stage-2-build-1").status_code == 200
    home = client.get("/").text
    assert "Build 1: A loaf" in home[home.index('<aside class="hero-card">') : home.index("</aside>")]


def test_the_mentor_opens_a_course_for_one_member(client, sign_in, session_factory, add_member, pastry):
    add_member("bob")
    _as_boss(sign_in)
    page = client.get("/admin/progress/alice").text
    assert f'action="/admin/progress/alice/courses/{pastry}/open"' in page and "Open this course for them" in page
    opened = client.post(f"/admin/progress/alice/courses/{pastry}/open", follow_redirects=False)
    assert opened.status_code == 303 and opened.headers["location"] == "/admin/progress/alice#course-pastry"
    # Twice is the same as once.
    client.post(f"/admin/progress/alice/courses/{pastry}/open")
    with session_factory() as db:
        assert db.query(CourseGrant).count() == 1
    page = client.get("/admin/progress/alice").text
    assert "Opened for them on" in page and f'action="/admin/progress/alice/courses/{pastry}/close"' in page

    _as_alice(sign_in)
    # The foot of the course she is in now says the next one is open, and leads into it.
    bread = client.get("/learn/bread").text
    after = bread[bread.index('<div class="course-after">') :]
    assert "Open now" in after and '<a class="continue-go" href="/learn/pastry/stage-2-chunk-1">Start Pastry</a>' in after
    assert client.get("/learn/pastry/stage-2-chunk-1").status_code == 200
    assert client.post("/learn/pastry/stage-2-chunk-1/done", follow_redirects=False).status_code == 303
    # Only that course. The one after it still waits for Pastry.
    assert client.get("/learn/cakes/stage-3-chunk-1", follow_redirects=False).status_code == 303
    # And only for her.
    sign_in("bob", 43)
    assert client.get("/learn/pastry/stage-2-chunk-1", follow_redirects=False).status_code == 303


def test_taking_the_key_back_locks_a_course_that_was_not_started(client, sign_in, session_factory, pastry):
    _as_boss(sign_in)
    client.post(f"/admin/progress/alice/courses/{pastry}/open")
    closed = client.post(f"/admin/progress/alice/courses/{pastry}/close", follow_redirects=False)
    assert closed.status_code == 303
    with session_factory() as db:
        assert db.query(CourseGrant).count() == 0
    _as_alice(sign_in)
    assert client.get("/learn/pastry/stage-2-chunk-1", follow_redirects=False).status_code == 303
    # Once she has ticked something in it, it stays open whatever happens to the key.
    _as_boss(sign_in)
    client.post(f"/admin/progress/alice/courses/{pastry}/open")
    _as_alice(sign_in)
    client.post("/learn/pastry/stage-2-chunk-1/done")
    _as_boss(sign_in)
    client.post(f"/admin/progress/alice/courses/{pastry}/close")
    _as_alice(sign_in)
    assert client.get("/learn/pastry/stage-2-build-1").status_code == 200


def test_only_a_course_members_can_see_is_opened(client, sign_in, session_factory, pastry):
    with session_factory() as db:
        db.add(CourseStage(number=9, slug="secret", title="Secret", visibility="draft"))
        db.commit()
    secret = _id(session_factory, CourseStage, slug="secret")
    _as_boss(sign_in)
    for path in (f"alice/courses/{secret}", "alice/courses/999", f"nobody/courses/{pastry}"):
        assert client.post(f"/admin/progress/{path}/open").status_code == 404, path
    with session_factory() as db:
        assert db.query(CourseGrant).count() == 0


def test_the_mentors_actions_are_closed_to_members_and_visitors(client, sign_in, session_factory, loaf, pastry):
    client.post("/learn/bread/stage-1-build-1/done", data=WORK)
    paths = (
        f"/admin/progress/alice/labs/{loaf}/send-back",
        f"/admin/progress/alice/labs/{loaf}/take-back",
        f"/admin/progress/alice/courses/{pastry}/open",
        f"/admin/progress/alice/courses/{pastry}/close",
    )
    for path in paths:
        assert client.post(path, data=NOTE).status_code == 403, path
    client.get("/auth/logout")
    client.cookies.clear()
    for path in paths:
        assert client.post(path, data=NOTE).status_code == 401, path
    assert _ticks(session_factory) == {"stage-1-build-1": WORK["link"]}
    with session_factory() as db:
        assert db.query(CourseGrant).count() == 0 and db.query(LabReturn).count() == 0


def test_removing_a_member_removes_their_notes_and_keys(client, sign_in, session_factory, loaf, pastry):
    client.post("/learn/bread/stage-1-build-1/done", data=WORK)
    _as_boss(sign_in)
    client.post(f"/admin/progress/alice/labs/{loaf}/send-back", data=NOTE)
    client.post(f"/admin/progress/alice/courses/{pastry}/open")
    with session_factory() as db:
        # SQLite only follows ON DELETE rules when asked to; Postgres always does.
        db.execute(text("PRAGMA foreign_keys = ON"))
        db.delete(db.query(Member).filter_by(github_login="alice").one())
        db.commit()
        assert db.query(LabReturn).count() == 0 and db.query(CourseGrant).count() == 0
        assert db.query(PageProgress).count() == 0

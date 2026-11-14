"""Courses: who sees which course and step, and where their pages are served."""

import datetime as dt

import pytest

from infra_portal.models import CourseStage, LabPage

DAY = dt.date(2026, 11, 12)


@pytest.fixture
def path(session_factory):
    """Stage 0 is only a sentence. Stage 1 has two chunks, a build and an unwritten build.
    Stage 2 has nothing. Stage 3 is a draft with one chunk."""
    with session_factory() as db:
        zero = CourseStage(number=0, slug="basics", title="Basics", visibility="members", summary="Do the [oven lab](/labs/ci) first.")
        one = CourseStage(number=1, slug="bread", title="Bread", visibility="members", summary="Flour, water and *time*.")
        two = CourseStage(number=2, slug="pastry", title="Pastry", visibility="members")
        three = CourseStage(number=3, slug="secret-stage", title="Secret stage", visibility="draft")
        db.add_all([zero, one, two, three])
        db.flush()

        def page(slug, title, kind, position, visibility="members", body="Text.", stage=one, minutes=None):
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

        page("stage-1-chunk-1", "Flour", "chunk", 1, minutes=23)
        page("stage-1-chunk-2", "Hidden yeast", "chunk", 2, visibility="draft")
        page("stage-1-chunk-3", "Water", "chunk", 3, body="Then [[course/stage-1-build-1|the loaf]] and [[course/stage-9-x|far]].")
        page("stage-1-build-1", "A loaf", "build", 4)
        page("stage-1-build-2", "A baguette", "build", 5, body="")
        page("stage-3-chunk-1", "In the secret stage", "chunk", 1, stage=three)
        db.commit()


@pytest.fixture
def as_member(add_member, sign_in):
    add_member("alice")
    sign_in("alice", 42)


@pytest.fixture
def as_admin(sign_in):
    sign_in("Boss", 1)


def test_signed_out_visitors_see_no_course_and_no_link_to_one(client, path):
    page = client.get("/learn")
    assert page.status_code == 200 and "for members" in page.text
    assert "Bread" not in page.text and "Flour" not in page.text
    assert 'href="/learn"' not in client.get("/").text
    assert client.get("/learn/bread").status_code == 404


def test_with_one_course_open_the_learn_page_is_that_course(client, path, as_member):
    page = client.get("/learn").text
    order = [page.index(name) for name in ("Bread</h1>", "Flour", "Water", "A loaf")]
    assert order == sorted(order)
    assert "Hidden yeast" not in page and "Secret stage" not in page
    # A course's summary is Markdown.
    assert "<em>time</em>" in page
    # The same course has an address of its own, and nothing sends the member to a list of one.
    assert "Bread</h1>" in client.get("/learn/bread").text
    assert "All courses" not in page
    assert '<a href="/learn"' in client.get("/").text


def test_a_course_with_nothing_written_is_not_shown_to_members(client, session_factory, path, as_member):
    # Pastry is approved but empty, and members see no trace of it.
    assert "Pastry" not in client.get("/learn").text
    # Not at its own address either, and neither does the course that is only a sentence.
    missing = client.get("/learn/no-such-course")
    for address in ("pastry", "basics"):
        page = client.get(f"/learn/{address}")
        assert page.status_code == 404 and page.text == missing.text, address
    # A link in the notes to its stage note stays plain text.
    with session_factory() as db:
        page = db.query(LabPage).filter_by(slug="stage-1-chunk-1").one()
        page.body = "Later: [[course/stage-2-pastry|the pastry stage]]."
        db.commit()
    text = client.get("/learn/bread/stage-1-chunk-1").text
    assert "the pastry stage" in text and "/learn/pastry" not in text


def test_an_admin_can_open_a_course_with_nothing_written(client, path, as_admin):
    assert "Pastry</h1>" in client.get("/learn/pastry").text


def test_a_course_that_is_only_a_sentence_is_what_comes_before(client, path, as_member):
    page = client.get("/learn").text
    assert "Before this course" in page and "Basics" in page
    # The sentence is Markdown, so it can link to a lab that is a file.
    assert '<a href="/labs/ci">oven lab</a>' in page


def test_a_course_is_split_into_sections_that_each_have_a_lab(client, path, as_member):
    page = client.get("/learn").text
    first = page[page.index('id="section-1"') :]
    # The lessons come first, then the lab they lead to, marked as a lab.
    order = [first.index(text) for text in ("2 lessons", "1 lab", "Flour", "Water", "pill-lab", "A loaf")]
    assert order == sorted(order)


def test_several_courses_make_a_list(client, session_factory, path, as_member):
    with session_factory() as db:
        pastry = db.query(CourseStage).filter_by(slug="pastry").one()
        db.add(
            LabPage(slug="stage-2-chunk-1", title="Butter", kind="chunk", date=DAY, summary="A summary.",
                    body="Text.", visibility="members", stage_id=pastry.id, position=1)
        )
        db.commit()
    page = client.get("/learn").text
    courses = page[page.index('<ol class="courses">') :]
    order = [courses.index(name) for name in ("Basics", 'href="/learn/bread"', 'href="/learn/pastry"')]
    assert order == sorted(order)
    # The list names each course. A course's outline is on its own page.
    assert 'class="lesson' not in courses
    pastry_page = client.get("/learn/pastry").text
    assert "Butter" in pastry_page and "All courses" in pastry_page


def test_a_hidden_step_keeps_its_number(client, path, as_member):
    # Chunk 2 is a draft. The next chunk is still "Chunk 3" for a member.
    page = client.get("/learn").text
    assert "Chunk 3" in page and "Chunk 2" not in page


def test_an_unwritten_step_is_listed_but_is_not_a_link(client, path, as_member):
    page = client.get("/learn").text
    assert "A baguette" in page and "Not written yet" in page
    assert 'href="/learn/bread/stage-1-build-2"' not in page
    assert "not written yet" in client.get("/learn/bread/stage-1-build-2").text


def test_an_old_step_address_moves_to_the_course(client, path, as_member):
    moved = client.get("/learn/stage-1-chunk-1", follow_redirects=False)
    assert moved.status_code == 308 and moved.headers["location"] == "/learn/bread/stage-1-chunk-1"
    # Not for a step this member may not see.
    assert client.get("/learn/stage-1-chunk-2", follow_redirects=False).status_code == 404


def test_a_course_without_a_slug_is_reached_by_its_number(client, session_factory, path, as_member):
    with session_factory() as db:
        db.query(CourseStage).filter_by(slug="bread").one().slug = None
        db.commit()
    assert "Bread</h1>" in client.get("/learn/stage-1").text
    assert client.get("/learn/stage-1/stage-1-chunk-1").status_code == 200


def test_an_admin_sees_drafts_marked_as_drafts(client, path, as_admin):
    page = client.get("/learn").text
    assert "Hidden yeast" in page and "Secret stage" in page and "Draft" in page
    assert client.get("/learn/bread/stage-1-chunk-2").status_code == 200
    # The empty course is not listed for an admin either. They reach it from the Course tab.
    assert "Pastry" not in page and "Pastry" in client.get("/admin/course").text


def test_hidden_missing_and_signed_out_get_the_same_404(client, path, add_member, sign_in):
    signed_out = client.get("/learn/bread/stage-1-chunk-1")
    add_member("alice")
    sign_in("alice", 42)
    hidden = client.get("/learn/bread/stage-1-chunk-2")
    hidden_stage = client.get("/learn/secret-stage/stage-3-chunk-1")
    missing = client.get("/learn/bread/no-such-page")
    no_course = client.get("/learn/no-such-course")
    assert {r.status_code for r in (signed_out, hidden, hidden_stage, missing, no_course)} == {404}
    assert signed_out.text == hidden.text == hidden_stage.text == missing.text == no_course.text


def test_step_page_links_to_the_steps_around_it(client, path, as_member):
    page = client.get("/learn/bread/stage-1-chunk-3").text
    # Earlier skips the hidden chunk; next is the build.
    assert 'href="/learn/bread/stage-1-chunk-1"' in page and 'href="/learn/bread/stage-1-build-1"' in page
    assert "Chunk 3" in page
    last = client.get("/learn/bread/stage-1-build-1").text
    # The last step that can be opened leads back to the course.
    assert "End of the course" in last[last.index('<nav class="step-nav"') :]


def test_step_page_keeps_the_whole_course_within_reach(client, path, as_member):
    page = client.get("/learn/bread/stage-1-chunk-3").text
    outline = page[page.index('<aside class="outline">') : page.index("</aside>")]
    # The course itself, every step that can be opened, and which one is open.
    assert 'href="/learn/bread"' in outline
    assert 'href="/learn/bread/stage-1-chunk-1"' in outline and 'href="/learn/bread/stage-1-build-1"' in outline
    assert 'href="/learn/bread/stage-1-chunk-3" aria-current="page"' in outline
    # An unwritten step is named but is not a link, and a hidden one is not named.
    assert "A baguette" in outline and "stage-1-build-2" not in outline and "Hidden yeast" not in outline


def test_links_between_notes_become_links_between_pages(client, path, as_member):
    page = client.get("/learn/bread/stage-1-chunk-3").text
    assert '<a href="/learn/bread/stage-1-build-1">the loaf</a>' in page
    # A note that is not a page here stays plain text.
    assert "far" in page and "stage-9-x" not in page and "[[" not in page


def test_a_link_to_a_hidden_page_stays_plain_text(client, session_factory, path, as_member):
    with session_factory() as db:
        page = db.query(LabPage).filter_by(slug="stage-1-chunk-1").one()
        page.body = "See [[course/stage-1-chunk-2|the yeast]] and [[course/stage-1-notes|the stage]]."
        db.commit()
    text = client.get("/learn/bread/stage-1-chunk-1").text
    assert "the yeast" in text and "/learn/bread/stage-1-chunk-2" not in text
    assert '<a href="/learn/bread">the stage</a>' in text


def test_path_pages_stay_out_of_the_lab_lists(client, path, as_member):
    home = client.get("/").text
    labs = client.get("/labs").text
    assert "Flour" not in labs and "Water" not in labs
    cards = home[home.index('<div class="lab-grid">') :]
    assert "A loaf" not in cards and "Flour" not in cards
    # Written builds get their own part of the labs page, under their course.
    assert "Labs in the course Bread" in labs and 'href="/learn/bread/stage-1-build-1"' in labs
    assert "A baguette" not in labs


def test_signed_out_labs_page_has_no_builds(client, path):
    labs = client.get("/labs").text
    assert "Labs in the course" not in labs and "A loaf" not in labs


def test_old_lab_address_of_a_path_page_moves_only_for_those_who_may_see_it(client, path, add_member, sign_in):
    assert client.get("/labs/stage-1-build-1", follow_redirects=False).status_code == 404
    add_member("alice")
    sign_in("alice", 42)
    moved = client.get("/labs/stage-1-build-1", follow_redirects=False)
    assert moved.status_code == 307 and moved.headers["location"] == "/learn/bread/stage-1-build-1"
    assert client.get("/labs/stage-1-chunk-2", follow_redirects=False).status_code == 404


def test_home_card_points_a_member_at_the_path(client, path, as_member):
    home = client.get("/").text
    card = home[home.index('<aside class="hero-card">') : home.index("</aside>")]
    assert "Chunk 1: Flour" in card and 'href="/learn/bread"' in card and "Bread" in card


def test_a_member_ticks_a_step_and_takes_it_back(client, path, as_member):
    assert "Mark as done" in client.get("/learn/bread/stage-1-chunk-1").text
    done = client.post("/learn/bread/stage-1-chunk-1/done", follow_redirects=False)
    assert done.status_code == 303 and done.headers["location"] == "/learn/bread/stage-1-chunk-1"
    # Ticking twice is the same as once.
    client.post("/learn/bread/stage-1-chunk-1/done")
    assert "Done on" in client.get("/learn/bread/stage-1-chunk-1").text
    page = client.get("/learn").text
    assert "1 of 3 done" in page and "Next up" in page and "Chunk 3: Water" in page
    client.post("/learn/bread/stage-1-chunk-1/undo")
    page = client.get("/learn").text
    assert "0 of 3 done" in page and "Start here" in page


def test_ticks_belong_to_one_member(client, path, add_member, sign_in):
    add_member("alice")
    add_member("bob")
    sign_in("alice", 42)
    client.post("/learn/bread/stage-1-chunk-1/done")
    sign_in("bob", 43)
    assert "0 of 3 done" in client.get("/learn").text


def test_a_finished_stage_says_done(client, path, as_member):
    for slug in ("stage-1-chunk-1", "stage-1-chunk-3", "stage-1-build-1"):
        client.post(f"/learn/bread/{slug}/done", data={"link": "https://example.com/my-loaf"})
    page = client.get("/learn").text
    assert "All 3 steps done" in page and "Continue" not in page


def test_only_a_visible_written_step_can_be_ticked(client, session_factory, path, add_member, sign_in):
    from infra_portal.models import PageProgress

    assert client.post("/learn/bread/stage-1-chunk-1/done").status_code == 401
    add_member("alice")
    sign_in("alice", 42)
    for slug in ("bread/stage-1-chunk-2", "bread/stage-1-build-2", "secret-stage/stage-3-chunk-1", "bread/nope", "pastry/stage-1-chunk-1"):
        assert client.post(f"/learn/{slug}/done").status_code == 404, slug
        assert client.post(f"/learn/{slug}/undo").status_code == 404, slug
    with session_factory() as db:
        assert db.query(PageProgress).count() == 0


def test_home_card_follows_the_members_next_step(client, path, as_member):
    client.post("/learn/bread/stage-1-chunk-1/done")
    home = client.get("/").text
    card = home[home.index('<aside class="hero-card">') : home.index("</aside>")]
    assert "Chunk 3: Water" in card

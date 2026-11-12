"""The learning path: who sees which stage and step, and where path pages are served."""

import datetime as dt

import pytest

from infra_portal.models import CourseStage, LabPage

DAY = dt.date(2026, 11, 12)


@pytest.fixture
def path(session_factory):
    """Stage 1 with two chunks, a build and an unwritten build; stage 2 with nothing; a draft stage 3."""
    with session_factory() as db:
        one = CourseStage(number=1, title="Bread", visibility="members", summary="See the [oven lab](/labs/ci).")
        two = CourseStage(number=2, title="Pastry", visibility="members")
        three = CourseStage(number=3, title="Secret stage", visibility="draft")
        db.add_all([one, two, three])
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


def test_signed_out_visitors_see_no_path_and_no_link_to_it(client, path):
    page = client.get("/learn")
    assert page.status_code == 200 and "for members" in page.text
    assert "Bread" not in page.text and "Flour" not in page.text
    assert 'href="/learn"' not in client.get("/").text


def test_a_member_sees_approved_stages_and_steps_in_order(client, path, as_member):
    page = client.get("/learn").text
    stages = page[page.index('<ol class="path">') :]
    order = [stages.index(name) for name in ("Bread", "Flour", "Water", "A loaf", "Pastry")]
    assert order == sorted(order)
    assert "Hidden yeast" not in page and "Secret stage" not in page
    # The stage's summary is Markdown, so it can link to a lab that is a file.
    assert '<a href="/labs/ci">oven lab</a>' in page
    assert '<a href="/learn"' in client.get("/").text


def test_a_hidden_step_keeps_its_number(client, path, as_member):
    # Chunk 2 is a draft. The next chunk is still "Chunk 3" for a member.
    page = client.get("/learn").text
    assert "Chunk 3" in page and "Chunk 2" not in page


def test_an_unwritten_step_is_listed_but_is_not_a_link(client, path, as_member):
    page = client.get("/learn").text
    assert "A baguette" in page and "Not written yet" in page
    assert 'href="/learn/stage-1-build-2"' not in page
    assert "not written yet" in client.get("/learn/stage-1-build-2").text


def test_an_admin_sees_drafts_marked_as_drafts(client, path, as_admin):
    page = client.get("/learn").text
    assert "Hidden yeast" in page and "Secret stage" in page and "Draft" in page
    assert client.get("/learn/stage-1-chunk-2").status_code == 200


def test_hidden_missing_and_signed_out_get_the_same_404(client, path, add_member, sign_in):
    signed_out = client.get("/learn/stage-1-chunk-1")
    add_member("alice")
    sign_in("alice", 42)
    hidden = client.get("/learn/stage-1-chunk-2")
    hidden_stage = client.get("/learn/stage-3-chunk-1")
    missing = client.get("/learn/no-such-page")
    assert {r.status_code for r in (signed_out, hidden, hidden_stage, missing)} == {404}
    assert signed_out.text == hidden.text == hidden_stage.text == missing.text


def test_step_page_links_to_the_steps_around_it(client, path, as_member):
    page = client.get("/learn/stage-1-chunk-3").text
    # Earlier skips the hidden chunk; next is the build.
    assert 'href="/learn/stage-1-chunk-1"' in page and 'href="/learn/stage-1-build-1"' in page
    assert "Stage 1: Bread" in page and "Chunk 3" in page
    last = client.get("/learn/stage-1-build-1").text
    assert "Next" not in last[last.index('<nav class="step-nav"') :]


def test_links_between_notes_become_links_between_pages(client, path, as_member):
    page = client.get("/learn/stage-1-chunk-3").text
    assert '<a href="/learn/stage-1-build-1">the loaf</a>' in page
    # A note that is not a page here stays plain text.
    assert "far" in page and "stage-9-x" not in page and "[[" not in page


def test_a_link_to_a_hidden_page_stays_plain_text(client, session_factory, path, as_member):
    with session_factory() as db:
        page = db.query(LabPage).filter_by(slug="stage-1-chunk-1").one()
        page.body = "See [[course/stage-1-chunk-2|the yeast]] and [[course/stage-1-notes|the stage]]."
        db.commit()
    text = client.get("/learn/stage-1-chunk-1").text
    assert "the yeast" in text and "/learn/stage-1-chunk-2" not in text
    assert '<a href="/learn#stage-1">the stage</a>' in text


def test_path_pages_stay_out_of_the_lab_lists(client, path, as_member):
    home = client.get("/").text
    labs = client.get("/labs").text
    assert "Flour" not in labs and "Water" not in labs
    cards = home[home.index('<div class="lab-grid">') :]
    assert "A loaf" not in cards and "Flour" not in cards
    # Written builds get their own part of the labs page.
    assert "Builds from the learning path" in labs and 'href="/learn/stage-1-build-1"' in labs
    assert "A baguette" not in labs


def test_signed_out_labs_page_has_no_builds(client, path):
    labs = client.get("/labs").text
    assert "Builds from the learning path" not in labs and "A loaf" not in labs


def test_old_lab_address_of_a_path_page_moves_only_for_those_who_may_see_it(client, path, add_member, sign_in):
    assert client.get("/labs/stage-1-build-1", follow_redirects=False).status_code == 404
    add_member("alice")
    sign_in("alice", 42)
    moved = client.get("/labs/stage-1-build-1", follow_redirects=False)
    assert moved.status_code == 307 and moved.headers["location"] == "/learn/stage-1-build-1"
    assert client.get("/labs/stage-1-chunk-2", follow_redirects=False).status_code == 404


def test_home_card_points_a_member_at_the_path(client, path, as_member):
    home = client.get("/").text
    card = home[home.index('<aside class="hero-card">') : home.index("</aside>")]
    assert "Chunk 1: Flour" in card and 'href="/learn"' in card

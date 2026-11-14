"""The admin area: the overview, the Course tab, stages, and approving steps."""

import datetime as dt

import pytest
from sqlalchemy import select

from infra_portal.models import CourseStage, LabPage, PageProgress

DAY = dt.date(2026, 11, 12)
PAGE_FORM = {
    "title": "Flour",
    "kind": "chunk",
    "date": "2026-11-12",
    "summary": "About flour.",
    "body": "Text.",
    "visibility": "draft",
}


@pytest.fixture
def boss(sign_in):
    sign_in("boss", github_id=1)


@pytest.fixture
def stage(session_factory):
    """A draft stage 1 with a written draft chunk, a shown chunk and an unwritten build."""
    with session_factory() as db:
        stage = CourseStage(number=1, slug="bread", title="Bread", visibility="draft")
        db.add(stage)
        db.flush()
        for slug, title, kind, position, visibility, body in (
            ("stage-1-chunk-1", "Flour", "chunk", 1, "draft", "Text."),
            ("stage-1-chunk-2", "Water", "chunk", 2, "members", "Text."),
            ("stage-1-build-1", "A loaf", "build", 3, "draft", ""),
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
                )
            )
        db.commit()
        return stage.id


def _page(session_factory, slug):
    with session_factory() as db:
        return db.scalar(select(LabPage).where(LabPage.slug == slug))


def _stage(session_factory, number):
    with session_factory() as db:
        return db.scalar(select(CourseStage).where(CourseStage.number == number))


ADMIN_PAGES = (
    "/admin",
    "/admin/course",
    "/admin/course/stages/new",
    "/admin/progress",
    "/admin/progress/boss",
    "/admin/labs",
    "/admin/members",
    "/admin/mail",
)


def test_every_admin_page_has_the_tabs_and_marks_its_own(client, boss):
    for path, name in (
        ("/admin", "Overview"),
        ("/admin/course", "Course"),
        ("/admin/progress", "Progress"),
        ("/admin/labs", "Pages"),
        ("/admin/members", "Members"),
        ("/admin/mail", "Email"),
    ):
        page = client.get(path)
        assert page.status_code == 200, path
        tabs = page.text[page.text.index('<nav class="admin-tabs"') : page.text.index("</nav>", page.text.index('<nav class="admin-tabs"'))]
        assert f'<a href="{path}" aria-current="page">{name}</a>' in tabs, path
        assert tabs.count("aria-current") == 1


def test_admin_pages_are_closed_to_members_and_visitors(client, stage, add_member, sign_in):
    writes = (
        "/admin/course/stages/new",
        f"/admin/course/stages/{stage}",
        f"/admin/course/stages/{stage}/show",
        f"/admin/course/stages/{stage}/hide",
        f"/admin/course/stages/{stage}/delete",
        "/admin/course/pages/1/visibility",
    )
    for path in ADMIN_PAGES:
        assert client.get(path).status_code == 401, path
    for path in writes:
        assert client.post(path, data={"visibility": "members"}).status_code == 401, path
    add_member("alice")
    sign_in("alice", 42)
    for path in ADMIN_PAGES:
        assert client.get(path).status_code == 403, path
    for path in writes:
        assert client.post(path, data={"visibility": "members"}).status_code == 403, path
    assert _stage_is_untouched(client)


def _stage_is_untouched(client) -> bool:
    return "Bread" not in client.get("/learn").text


def test_overview_counts_people_steps_and_what_is_waiting(client, stage, boss, add_member, session_factory):
    add_member("invited-only")
    page = client.get("/admin").text
    # Nothing of a draft stage is open to members, so both written steps are waiting.
    assert "<strong>0</strong>" in page and "2 written steps are still hidden" in page
    client.post(f"/admin/course/stages/{stage}/show")
    page = client.get("/admin").text
    assert "<strong>2</strong>" in page and "still hidden" not in page
    assert "1 invited, not signed in yet" in page


def test_overview_shows_where_each_member_is(client, stage, add_member, sign_in, session_factory):
    add_member("alice", display_name="Alice")
    sign_in("boss", 1)
    client.post(f"/admin/course/stages/{stage}/show")
    sign_in("alice", 42)
    client.post("/learn/bread/stage-1-chunk-1/done")
    sign_in("boss", 1)
    page = client.get("/admin").text
    row = page[page.index("Alice") :]
    row = row[: row.index("</tr>")]
    assert "1 of 2" in row and "Bread · Chunk 2" in row
    # The admin has ticked nothing.
    assert "0 of 2" in page


def test_course_tab_lists_stages_with_their_steps_in_order(client, stage, boss):
    page = client.get("/admin/course").text
    order = [page.index(name) for name in ("Bread", "Flour", "Water", "A loaf")]
    assert order == sorted(order)
    assert "not written yet" in page and "Show the stage and 1 written step to members" in page


def test_show_approves_the_stage_and_its_written_drafts_only(client, stage, boss, session_factory):
    response = client.post(f"/admin/course/stages/{stage}/show", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/admin/course#stage-1"
    assert _stage(session_factory, 1).visibility == "members"
    assert _page(session_factory, "stage-1-chunk-1").visibility == "members"
    # The build has no text yet, so it stays a draft.
    assert _page(session_factory, "stage-1-build-1").visibility == "draft"


def test_hiding_a_stage_takes_it_from_members_at_once(client, stage, add_member, sign_in):
    sign_in("boss", 1)
    client.post(f"/admin/course/stages/{stage}/show")
    add_member("alice")
    sign_in("alice", 42)
    assert client.get("/learn/bread/stage-1-chunk-1").status_code == 200
    sign_in("boss", 1)
    client.post(f"/admin/course/stages/{stage}/hide")
    sign_in("alice", 42)
    assert client.get("/learn/bread/stage-1-chunk-1").status_code == 404
    assert "Bread" not in client.get("/learn").text


def test_one_step_can_be_shown_or_hidden(client, stage, boss, session_factory):
    page = _page(session_factory, "stage-1-chunk-1")
    client.post(f"/admin/course/pages/{page.id}/visibility", data={"visibility": "members"})
    assert _page(session_factory, "stage-1-chunk-1").visibility == "members"
    client.post(f"/admin/course/pages/{page.id}/visibility", data={"visibility": "draft"})
    assert _page(session_factory, "stage-1-chunk-1").visibility == "draft"
    assert client.post(f"/admin/course/pages/{page.id}/visibility", data={"visibility": "everyone"}).status_code == 422
    assert client.post("/admin/course/pages/9999/visibility", data={"visibility": "draft"}).status_code == 404


def test_admin_adds_and_edits_a_stage(client, boss, session_factory):
    form = client.get("/admin/course/stages/new").text
    assert 'name="number" type="number" min="0" max="99" required value="0"' in form
    created = client.post(
        "/admin/course/stages/new",
        data={"number": "2", "title": "Pastry", "summary": "", "visibility": "draft"},
        follow_redirects=False,
    )
    assert created.status_code == 303
    stage = _stage(session_factory, 2)
    assert stage.title == "Pastry" and stage.visibility == "draft"
    # The course's address comes from its first title.
    assert stage.slug == "pastry"
    client.post(
        f"/admin/course/stages/{stage.id}",
        data={"number": "3", "title": "Sweet pastry", "summary": "With [a link](/labs/ci).", "visibility": "members"},
    )
    stage = _stage(session_factory, 3)
    assert stage.title == "Sweet pastry" and stage.visibility == "members"
    # A new title or number does not move the course, so links to it keep working.
    assert stage.slug == "pastry"
    assert "/learn/pastry" in client.get(f"/admin/course/stages/{stage.id}").text


def test_a_new_stage_never_takes_an_address_that_is_in_use(client, stage, boss, session_factory):
    # "Bread" is a course already. A step page is called stage-1-chunk-1, and a step used to be
    # served at /learn/<step>. "Stage 7" would look like a course that has no slug.
    long_title = "Sourdough from a starter you kept alive for a whole month, then croissants"
    for number, title in ((2, "Bread"), (3, "Bread!"), (4, "Stage 1 chunk 1"), (5, "Stage 7"), (6, "???"), (7, long_title)):
        client.post(
            "/admin/course/stages/new",
            data={"number": str(number), "title": title, "summary": "", "visibility": "draft"},
        )
    slugs = [_stage(session_factory, number).slug for number in (2, 3, 4, 5, 6)]
    assert slugs == ["bread-2", "bread-3", "stage-1-chunk-1-2", "stage-7-2", "course"]
    # A long title is cut at the end of a word.
    assert _stage(session_factory, 7).slug == "sourdough-from-a-starter-you-kept-alive-for-a-whole-month"


@pytest.mark.parametrize(
    "change, message",
    [
        ({"number": "x"}, "whole number"),
        ({"number": "100"}, "whole number"),
        ({"number": "1"}, "already a stage 1"),
        ({"title": ""}, "title is required"),
        ({"summary": "s" * 301}, "at most 300"),
        ({"visibility": "public"}, "Choose who can see"),
    ],
)
def test_a_bad_stage_form_says_why_and_saves_nothing(client, stage, boss, session_factory, change, message):
    data = {"number": "5", "title": "Pastry", "summary": "", "visibility": "draft", **change}
    response = client.post("/admin/course/stages/new", data=data)
    assert response.status_code == 422 and message in response.text
    with session_factory() as db:
        assert db.query(CourseStage).count() == 1


def test_deleting_a_stage_keeps_its_pages_as_hidden_drafts(client, stage, boss, session_factory):
    client.post(f"/admin/course/stages/{stage}/show")
    client.post(f"/admin/course/stages/{stage}/delete")
    assert _stage(session_factory, 1) is None
    for slug in ("stage-1-chunk-1", "stage-1-chunk-2", "stage-1-build-1"):
        page = _page(session_factory, slug)
        assert page.stage_id is None and page.visibility == "draft"
    # Nothing that was members-only on the path turns up in the public lab list.
    client.post("/auth/logout")
    assert "Flour" not in client.get("/labs").text


def test_the_page_editor_puts_a_page_on_the_path_and_keeps_it_there(client, stage, boss, session_factory):
    created = client.post(
        "/admin/labs/new",
        data={**PAGE_FORM, "slug": "stage-1-chunk-9", "title": "Salt", "stage": str(stage), "position": "7", "number": "9", "minutes": "12"},
        follow_redirects=False,
    )
    assert created.status_code == 303, created.text
    page = _page(session_factory, "stage-1-chunk-9")
    assert (page.stage_id, page.position, page.number, page.minutes) == (stage, 7, 9, 12)
    assert 'name="number" type="number" min="0" max="999" value="9"' in client.get(f"/admin/labs/{page.id}/edit").text
    # The form shows what is stored, so saving it again changes nothing.
    form = client.get(f"/admin/labs/{page.id}/edit").text
    assert f'<option value="{stage}" selected>Stage 1: Bread</option>' in form
    assert 'name="position" type="number" min="0" max="999" value="7"' in form
    assert 'name="minutes" type="number" min="0" max="999" value="12"' in form
    assert "/learn/bread/stage-1-chunk-9" in form
    # A preview keeps the three fields too.
    preview = client.post(
        f"/admin/labs/{page.id}/edit",
        data={**PAGE_FORM, "title": "Salt", "stage": str(stage), "position": "7", "minutes": "12", "action": "preview"},
    ).text
    assert f'<option value="{stage}" selected>' in preview and 'value="7"' in preview


@pytest.mark.parametrize(
    "change, message",
    [
        ({"stage": "9999"}, "Choose a stage"),
        ({"stage": "x"}, "Choose a stage"),
        ({"position": "-1"}, "place in the stage"),
        ({"position": "1000"}, "place in the stage"),
        ({"minutes": "many"}, "Minutes of video"),
        ({"number": "third"}, "s number must be a whole number"),
    ],
)
def test_the_page_editor_refuses_bad_path_fields(client, stage, boss, session_factory, change, message):
    data = {**PAGE_FORM, "slug": "new-page", "stage": str(stage), "position": "1", "number": "", "minutes": "", **change}
    response = client.post("/admin/labs/new", data=data)
    assert response.status_code == 422 and message in response.text
    assert _page(session_factory, "new-page") is None


def test_removing_a_member_removes_their_ticks(client, stage, add_member, sign_in, session_factory):
    from sqlalchemy import text

    alice = add_member("alice")
    sign_in("boss", 1)
    client.post(f"/admin/course/stages/{stage}/show")
    sign_in("alice", 42)
    client.post("/learn/bread/stage-1-chunk-1/done")
    sign_in("boss", 1)
    with session_factory() as db:
        # SQLite only follows ON DELETE rules when asked to; Postgres always does.
        db.execute(text("PRAGMA foreign_keys = ON"))
        db.delete(db.get(type(alice), alice.id))
        db.commit()
        assert db.query(PageProgress).count() == 0

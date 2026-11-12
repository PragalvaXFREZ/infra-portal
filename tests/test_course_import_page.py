"""The admin page that stores uploaded course notes. The reading itself is in test_course_import.py."""

import pytest
from sqlalchemy import select

from infra_portal.models import CourseStage, LabPage

STAGE_NOTE = """---
title: "Baking course, stage 2: bread"
description: "A plan for the mentor."
tags:
  - baking
---

Text for the mentor only: SECRET-PLAN.

## The builds

### Build 1. A first loaf

An outline for the mentor: SECRET-OUTLINE.

### Build 2. A baguette (not written yet)

---

## Chunk 1. Flour

Flour is ground grain.

**Watch (12 min)**

- [A video](https://example.com/flour), 12:00

**Feeds:** build 1.

---

## Chunk 2. Water

Water wakes the flour.

**Watch (8 min)**

**Feeds:** build 2.

---

## The path

| Stage | Topic | State |
|---|---|---|
| 1 | Ovens | Done |
| 2 | Bread | Being written |
| 3 | Pastry | Not started |
"""

BUILD_NOTE = """---
title: "Stage 2, build 1: a first loaf"
description: "Bake one loaf and measure it."
---

Mix, wait, bake. See [[baking/stage-2-chunk-1|the flour chunk]].
"""

WARM_UP = """---
title: "Stage 2, chunk 1: warm-up"
---

Weigh 100 g of flour.
"""

ANSWERS = """---
title: "Stage 2, build 1: answers"
draft: true
---

The answer is SECRET-ANSWER.
"""


@pytest.fixture
def boss(sign_in):
    sign_in("boss", github_id=1)


def _upload(client, **files):
    parts = [("notes", (name, text.encode(), "text/markdown")) for name, text in files.items()]
    return client.post("/admin/course/import", files=parts)


def _pages(session_factory):
    with session_factory() as db:
        return {page.slug: page for page in db.scalars(select(LabPage))}


def test_an_upload_becomes_draft_stages_and_steps_in_order(client, boss, session_factory):
    response = _upload(
        client,
        **{"stage-2-bread.md": STAGE_NOTE, "stage-2-build-1.md": BUILD_NOTE, "stage-2-chunk-1.md": WARM_UP},
    )
    assert response.status_code == 200 and "Done." in response.text
    with session_factory() as db:
        stages = {s.number: s for s in db.scalars(select(CourseStage))}
    assert {n: s.title for n, s in stages.items()} == {1: "Ovens", 2: "Bread", 3: "Pastry"}
    assert {s.visibility for s in stages.values()} == {"draft"}
    pages = _pages(session_factory)
    order = sorted(pages.values(), key=lambda p: p.position)
    assert [p.slug for p in order] == ["stage-2-chunk-1", "stage-2-build-1", "stage-2-chunk-2", "stage-2-build-2"]
    assert {p.visibility for p in order} == {"draft"} and {p.stage_id for p in order} == {stages[2].id}
    assert (pages["stage-2-chunk-1"].kind, pages["stage-2-chunk-1"].minutes) == ("chunk", 12)
    assert "Weigh 100 g" in pages["stage-2-chunk-1"].body
    assert pages["stage-2-build-1"].title == "A first loaf" and "Mix, wait, bake." in pages["stage-2-build-1"].body
    # The build that is only named has no text, and says so in its summary.
    assert pages["stage-2-build-2"].body == "" and pages["stage-2-build-2"].summary == "Not written yet."


def test_text_for_the_mentor_and_answer_keys_are_never_stored(client, boss, session_factory):
    response = _upload(
        client,
        **{"stage-2-bread.md": STAGE_NOTE, "stage-2-build-1-mentor.md": BUILD_NOTE, "answers.md": ANSWERS},
    )
    assert "stage-2-build-1-mentor.md" in response.text and "answers.md" in response.text
    stored = " ".join(page.title + page.summary + page.body for page in _pages(session_factory).values())
    assert "SECRET" not in stored and "SECRET" not in response.text
    # The refused build note did not make the build: it is still only a name.
    assert _pages(session_factory)["stage-2-build-1"].body == ""


def test_members_see_nothing_until_an_admin_shows_it(client, boss, add_member, sign_in, session_factory):
    _upload(client, **{"stage-2-bread.md": STAGE_NOTE, "stage-2-build-1.md": BUILD_NOTE})
    add_member("alice")
    sign_in("alice", 42)
    assert "Bread" not in client.get("/learn").text
    assert client.get("/learn/stage-2-chunk-1").status_code == 404
    sign_in("boss", 1)
    with session_factory() as db:
        stage = db.scalar(select(CourseStage.id).where(CourseStage.number == 2))
    client.post(f"/admin/course/stages/{stage}/show")
    sign_in("alice", 42)
    page = client.get("/learn").text
    assert "Flour" in page and "A first loaf" in page and "A baguette" not in page
    # The link between two notes is a link between two pages.
    assert '<a href="/learn/stage-2-chunk-1">the flour chunk</a>' in client.get("/learn/stage-2-build-1").text


def test_uploading_again_replaces_text_and_keeps_who_sees_it_and_the_ticks(client, boss, add_member, sign_in, session_factory):
    _upload(client, **{"stage-2-bread.md": STAGE_NOTE, "stage-2-build-1.md": BUILD_NOTE})
    with session_factory() as db:
        stage = db.scalar(select(CourseStage).where(CourseStage.number == 2))
        stage.title = "Bread, edited here"
        db.commit()
        stage_id = stage.id
    client.post(f"/admin/course/stages/{stage_id}/show")
    add_member("alice")
    sign_in("alice", 42)
    client.post("/learn/stage-2-chunk-1/done")
    sign_in("boss", 1)

    # The second upload has new chunk text and no build note.
    response = _upload(client, **{"stage-2-bread.md": STAGE_NOTE.replace("ground grain", "milled grain")})
    assert "Steps replaced" in response.text and "New steps" not in response.text
    pages = _pages(session_factory)
    assert "milled grain" in pages["stage-2-chunk-1"].body and pages["stage-2-chunk-1"].visibility == "members"
    # The build's guide is still there: a name alone does not wipe it.
    assert "Mix, wait, bake." in pages["stage-2-build-1"].body
    with session_factory() as db:
        assert db.get(CourseStage, stage_id).title == "Bread, edited here"
    sign_in("alice", 42)
    assert "1 of 3 done" in client.get("/learn").text


def test_a_page_that_would_take_a_lab_files_address_is_left_out(client, boss, session_factory, monkeypatch):
    from infra_portal import course_admin

    monkeypatch.setattr(course_admin, "file_slugs", lambda: {"stage-2-chunk-1"})
    response = _upload(client, **{"stage-2-bread.md": STAGE_NOTE})
    assert "already uses the address stage-2-chunk-1" in response.text
    assert "stage-2-chunk-1" not in _pages(session_factory)


def test_files_that_cannot_be_read_are_named(client, boss, session_factory):
    parts = [
        ("notes", ("big.md", b"x" * 400_001, "text/markdown")),
        ("notes", ("binary.md", b"\xff\xfe\x00bad", "text/markdown")),
        ("notes", ("shopping.md", b"Eggs and milk.", "text/markdown")),
    ]
    response = client.post("/admin/course/import", files=parts)
    assert response.status_code == 200 and "Nothing was stored." in response.text
    assert "larger than 400 kB" in response.text and "not UTF-8" in response.text and "shopping.md" in response.text
    assert _pages(session_factory) == {}


def test_an_empty_upload_and_too_many_files_are_refused(client, boss):
    assert client.post("/admin/course/import").status_code == 422
    parts = [("notes", (f"n{i}.md", b"x", "text/markdown")) for i in range(41)]
    assert client.post("/admin/course/import", files=parts).status_code == 422


def test_only_admins_reach_the_import_page(client, add_member, sign_in):
    assert client.get("/admin/course/import").status_code == 401
    add_member("alice")
    sign_in("alice", 42)
    assert client.get("/admin/course/import").status_code == 403
    assert _upload(client, **{"stage-2-bread.md": STAGE_NOTE}).status_code == 403


def test_an_answer_key_with_a_very_long_name_is_still_refused(client, boss, session_factory):
    name = "x" * 200 + "-mentor.md"
    response = _upload(client, **{name: BUILD_NOTE})
    assert "file name is longer than 200" in response.text and "Nothing was stored." in response.text
    assert _pages(session_factory) == {}


CROSSED = STAGE_NOTE.replace(
    "## The path",
    "## Chunk 3. Salt\n\nSalt slows the yeast.\n\n**Feeds:** build 1.\n\n---\n\n## The path",
)


def test_a_step_keeps_its_own_number_when_the_order_differs(client, boss, session_factory):
    # Chunk 3 feeds build 1, so it comes before chunk 2 on the path. It is still chunk 3.
    _upload(client, **{"stage-2-bread.md": CROSSED})
    pages = _pages(session_factory)
    order = [p.slug for p in sorted(pages.values(), key=lambda p: p.position)]
    assert order.index("stage-2-chunk-3") < order.index("stage-2-chunk-2")
    assert (pages["stage-2-chunk-3"].number, pages["stage-2-chunk-2"].number) == (3, 2)
    assert "Chunk 3" in client.get("/learn/stage-2-chunk-3").text
    assert "Chunk 2" in client.get("/learn/stage-2-chunk-2").text


def test_uploading_one_build_note_again_keeps_the_order(client, boss, session_factory):
    _upload(client, **{"stage-2-bread.md": STAGE_NOTE, "stage-2-build-1.md": BUILD_NOTE})
    before = {slug: page.position for slug, page in _pages(session_factory).items()}
    _upload(client, **{"stage-2-build-1.md": BUILD_NOTE.replace("Mix, wait", "Mix, rest")})
    pages = _pages(session_factory)
    assert {slug: page.position for slug, page in pages.items()} == before
    assert "Mix, rest" in pages["stage-2-build-1"].body


def test_a_build_note_for_a_stage_without_its_stage_note_goes_last(client, boss, session_factory):
    _upload(client, **{"stage-2-bread.md": STAGE_NOTE})
    third = BUILD_NOTE.replace("build 1: a first loaf", "build 3: a sourdough")
    _upload(client, **{"stage-2-build-3.md": third})
    pages = _pages(session_factory)
    assert pages["stage-2-build-3"].position == max(page.position for page in pages.values())
    assert sorted(page.position for page in pages.values()) == [1, 2, 3, 4, 5]


def test_uploading_the_stage_note_alone_keeps_a_warm_up_that_is_already_there(client, boss, session_factory):
    _upload(client, **{"stage-2-bread.md": STAGE_NOTE, "stage-2-chunk-1.md": WARM_UP})
    _upload(client, **{"stage-2-bread.md": STAGE_NOTE.replace("ground grain", "milled grain")})
    body = _pages(session_factory)["stage-2-chunk-1"].body
    assert "milled grain" in body and body.count("Weigh 100 g") == 1
    # With both notes again the warm-up is the new one, once.
    _upload(client, **{"stage-2-bread.md": STAGE_NOTE, "stage-2-chunk-1.md": WARM_UP.replace("100 g", "250 g")})
    body = _pages(session_factory)["stage-2-chunk-1"].body
    assert "250 g" in body and "100 g" not in body and body.count("## Warm-up steps") == 1

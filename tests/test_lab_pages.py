"""Who sees which database lab on /labs, /labs/<slug> and the home page."""

from datetime import date

import pytest
from sqlalchemy import select

from infra_portal.labs import all_labs, can_see, visible_labs
from infra_portal.models import LabPage, Member

# Newer than every file lab, so the pages are in the home page's first three.
NEWEST = date(2099, 1, 1)


@pytest.fixture
def add_page(session_factory):
    def _add(slug: str, visibility: str, **fields) -> LabPage:
        values = {
            "title": f"Title of {slug}",
            "kind": "session",
            "date": NEWEST,
            "summary": f"Summary of {slug}.",
            "body": f"Body of {slug}.",
        }
        values.update(fields)
        with session_factory() as session:
            page = LabPage(slug=slug, visibility=visibility, **values)
            session.add(page)
            session.commit()
            return page

    return _add


@pytest.fixture
def pages(add_page):
    add_page("made-up-draft", "draft")
    add_page("made-up-members", "members")
    add_page("made-up-public", "public")


def _as_member(add_member, sign_in):
    add_member("alice")
    sign_in("alice", github_id=42)


def _as_admin(sign_in):
    sign_in("boss", github_id=1)


@pytest.mark.parametrize(
    ("visibility", "anonymous", "member", "admin"),
    [
        ("draft", False, False, True),
        ("members", False, True, True),
        ("public", True, True, True),
    ],
)
def test_can_see(visibility, anonymous, member, admin):
    assert can_see(visibility, None) is anonymous
    assert can_see(visibility, Member(github_login="alice", role="member")) is member
    assert can_see(visibility, Member(github_login="boss", role="admin")) is admin


def test_anonymous_sees_only_public_pages(client, pages):
    for url in ("/labs", "/"):
        text = client.get(url).text
        assert "Title of made-up-public" in text
        assert "Title of made-up-members" not in text
        assert "Title of made-up-draft" not in text

    assert client.get("/labs/made-up-public").status_code == 200
    assert client.get("/labs/made-up-members").status_code == 404
    assert client.get("/labs/made-up-draft").status_code == 404


def test_member_sees_member_pages_with_a_label(client, pages, add_member, sign_in):
    _as_member(add_member, sign_in)

    for url in ("/labs", "/"):
        text = client.get(url).text
        assert "Title of made-up-public" in text
        assert "Title of made-up-members" in text
        assert "Title of made-up-draft" not in text
        assert "Members only" in text
        assert ">Draft<" not in text

    page = client.get("/labs/made-up-members")
    assert page.status_code == 200
    assert "Members only" in page.text
    assert "Body of made-up-members." in page.text
    assert client.get("/labs/made-up-draft").status_code == 404


def test_admin_sees_everything_with_labels(client, pages, sign_in):
    _as_admin(sign_in)

    for url in ("/labs", "/"):
        text = client.get(url).text
        for slug in ("made-up-public", "made-up-members", "made-up-draft"):
            assert f"Title of {slug}" in text
        assert ">Draft<" in text
        assert ">Members only<" in text

    draft = client.get("/labs/made-up-draft")
    assert draft.status_code == 200
    assert "<strong>Draft</strong>" in draft.text
    assert "/edit\">Edit</a>" in draft.text
    assert "Members only" in client.get("/labs/made-up-members").text


def test_public_page_has_no_label(client, add_page):
    add_page("made-up-public", "public")

    text = client.get("/labs/made-up-public").text

    assert "Members only" not in text
    assert "<strong>Draft</strong>" not in text


def test_hidden_and_missing_slugs_give_the_same_404(client, pages, add_member, sign_in):
    hidden = client.get("/labs/made-up-draft")
    missing = client.get("/labs/no-such-page")
    assert hidden.status_code == missing.status_code == 404
    assert hidden.text == missing.text
    assert hidden.headers["content-type"] == missing.headers["content-type"]

    _as_member(add_member, sign_in)
    hidden = client.get("/labs/made-up-draft")
    assert (hidden.status_code, hidden.text) == (missing.status_code, missing.text)


def test_anonymous_hint_reveals_nothing(client, add_page):
    before = client.get("/labs").text
    add_page("made-up-draft", "draft")
    add_page("made-up-members", "members")
    after = client.get("/labs").text

    # The same page with and without hidden pages: no count, no title, no address.
    assert before == after
    assert "Members see more after" in after
    assert "made-up" not in after


def test_signed_in_viewers_get_no_hint(client, add_member, sign_in):
    _as_member(add_member, sign_in)

    assert "Members see more after" not in client.get("/labs").text


def test_file_and_database_labs_are_one_list_newest_first(session_factory, add_page):
    file_lab = all_labs()[0]
    add_page("made-up-old", "public", date=date(2000, 1, 1))
    add_page("made-up-new", "public", date=NEWEST)

    with session_factory() as session:
        slugs = [lab.slug for lab in visible_labs(session, None)]

    assert slugs[0] == "made-up-new"
    assert slugs[-1] == "made-up-old"
    assert file_lab.slug in slugs


def test_database_labs_are_read_per_request(client, session_factory, add_page):
    add_page("made-up-public", "public")
    assert "Title of made-up-public" in client.get("/labs").text

    with session_factory() as session:
        page = session.scalar(select(LabPage).where(LabPage.slug == "made-up-public"))
        page.visibility = "draft"
        session.commit()

    assert "Title of made-up-public" not in client.get("/labs").text
    assert client.get("/labs/made-up-public").status_code == 404


def test_a_file_lab_keeps_its_address(client, add_page):
    file_lab = all_labs()[0]
    # A row that clashes with a file, as if the file was added after the row.
    add_page(file_lab.slug, "public", title="Made-up clashing title")

    assert "Made-up clashing title" not in client.get("/labs").text
    page = client.get(file_lab.url)
    assert page.status_code == 200
    assert file_lab.summary in page.text

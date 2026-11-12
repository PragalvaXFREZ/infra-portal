"""Writing database labs on the admin pages."""

import re

import pytest
from sqlalchemy import select

from infra_portal.labs import all_labs
from infra_portal.models import LabPage, Member

FORM = {
    "title": "A made-up session",
    "slug": "made-up-session",
    "kind": "session",
    "date": "2099-01-01",
    "summary": "Made-up summary.",
    "link": "",
    "body": "Made-up **body**.",
    "visibility": "draft",
}


@pytest.fixture
def boss(sign_in):
    sign_in("boss", github_id=1)


def _create(client, **changes):
    response = client.post("/admin/labs/new", data={**FORM, **changes}, follow_redirects=False)
    assert response.status_code == 303, response.text
    return int(re.search(r"/admin/labs/(\d+)/edit", response.headers["location"]).group(1))


def _page(session_factory, page_id) -> LabPage | None:
    with session_factory() as session:
        return session.get(LabPage, page_id)


def test_admin_creates_a_draft(client, boss, session_factory):
    page_id = _create(client)

    page = _page(session_factory, page_id)
    assert (page.slug, page.title, page.kind, page.visibility) == (
        "made-up-session",
        "A made-up session",
        "session",
        "draft",
    )
    assert page.date.isoformat() == "2099-01-01"
    assert page.link is None
    with session_factory() as session:
        boss_id = session.scalar(select(Member.id).where(Member.github_login == "boss"))
    assert page.edited_by_id == boss_id

    listing = client.get("/admin/labs").text
    assert "A made-up session" in listing
    assert "Draft" in listing
    assert client.get("/labs/made-up-session").status_code == 200


def test_new_form_starts_as_a_draft(client, boss):
    form = client.get("/admin/labs/new").text

    assert '<option value="draft" selected>' in form
    assert 'data-preview-url="/admin/labs/preview"' in form
    assert 'Page <span class="muted small">(Markdown)</span>' in form


def test_post_editor_keeps_its_tabs_after_the_shared_template(client, add_member, sign_in):
    add_member("alice")
    sign_in("alice", github_id=42)

    form = client.get("/write").text

    assert 'Post <span class="muted small">(Markdown)</span>' in form
    assert 'value="preview" class="tab"' in form
    # Posts keep the default preview address.
    assert "data-preview-url" not in form


def test_an_empty_address_comes_from_the_title(client, boss, session_factory):
    page_id = _create(client, slug="", title="Made-up Title, Again")

    assert _page(session_factory, page_id).slug == "made-up-title-again"


def test_admin_edits_and_changes_visibility(client, boss, session_factory):
    page_id = _create(client)

    response = client.post(
        f"/admin/labs/{page_id}/edit",
        data={**FORM, "title": "Changed title", "slug": "other-address", "visibility": "members"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    page = _page(session_factory, page_id)
    assert page.title == "Changed title"
    assert page.visibility == "members"
    # The address is set once, at creation.
    assert page.slug == "made-up-session"

    client.post(f"/admin/labs/{page_id}/edit", data={**FORM, "visibility": "public"})
    assert _page(session_factory, page_id).visibility == "public"
    client.post("/auth/logout")
    assert client.get("/labs/made-up-session").status_code == 200


def test_admin_deletes_a_page(client, boss, session_factory):
    page_id = _create(client)

    response = client.post(f"/admin/labs/{page_id}/delete", follow_redirects=False)

    assert response.status_code == 303
    assert _page(session_factory, page_id) is None
    assert client.get("/labs/made-up-session").status_code == 404


def test_delete_needs_a_post(client, boss):
    page_id = _create(client)

    assert client.get(f"/admin/labs/{page_id}/delete").status_code == 405


def test_a_file_lab_address_is_rejected(client, boss, session_factory):
    file_slug = all_labs()[0].slug

    response = client.post("/admin/labs/new", data={**FORM, "slug": file_slug})

    assert response.status_code == 422
    assert "lab file in the repository already uses" in response.text
    # What was typed is kept.
    assert "Made-up **body**." in response.text
    with session_factory() as session:
        assert session.scalars(select(LabPage)).all() == []


def test_a_taken_address_is_rejected(client, boss):
    _create(client)

    response = client.post("/admin/labs/new", data=FORM)

    assert response.status_code == 422
    assert "Another page already uses" in response.text


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"title": " "}, "title is required"),
        ({"slug": "Not An Address!"}, "lowercase letters"),
        ({"kind": "talk"}, "Choose a kind"),
        ({"date": "soon"}, "YYYY-MM-DD"),
        ({"summary": ""}, "summary is required"),
        ({"link": "javascript:alert(1)"}, "must start with https://"),
        ({"visibility": "everyone"}, "Choose who can see"),
    ],
)
def test_bad_fields_are_rejected(client, boss, changes, message):
    response = client.post("/admin/labs/new", data={**FORM, **changes})

    assert response.status_code == 422
    assert message in response.text


def test_preview_tab_saves_nothing_and_keeps_the_fields(client, boss, session_factory):
    response = client.post("/admin/labs/new", data={**FORM, "action": "preview"})

    assert response.status_code == 200
    assert "<strong>body</strong>" in response.text
    assert "Made-up summary." in response.text
    with session_factory() as session:
        assert session.scalars(select(LabPage)).all() == []


def test_preview_tab_on_an_existing_page_leaves_it_unchanged(client, boss, session_factory):
    page_id = _create(client)

    response = client.post(
        f"/admin/labs/{page_id}/edit",
        data={**FORM, "body": "# New heading", "visibility": "public", "action": "preview"},
    )

    assert response.status_code == 200
    assert "<h1>New heading</h1>" in response.text
    page = _page(session_factory, page_id)
    assert (page.body, page.visibility) == ("Made-up **body**.", "draft")

    write = client.post(f"/admin/labs/{page_id}/edit", data={**FORM, "body": "kept", "action": "write"})
    assert write.status_code == 200
    assert "kept" in write.text


def test_preview_route_renders_lab_markdown_and_escapes_html(client, boss):
    response = client.post(
        "/admin/labs/preview",
        data={"body": "[[a/b|Label]] <script>alert(1)</script>\n\n> [!note] <script>x</script>\n> y"},
    )

    assert response.status_code == 200
    assert "Label" in response.text
    assert "[[" not in response.text
    assert 'class="callout callout-note"' in response.text
    assert "<script>" not in response.text
    assert "&lt;script&gt;" in response.text


def test_removing_the_editor_keeps_the_page(client, boss, add_member, sign_in, session_factory):
    add_member("carol", role="admin")
    client.post("/auth/logout")
    sign_in("carol", github_id=77)
    page_id = _create(client)
    client.post("/auth/logout")
    sign_in("boss", github_id=1)
    with session_factory() as session:
        carol_id = session.scalar(select(Member.id).where(Member.github_login == "carol"))

    client.post(f"/admin/members/{carol_id}/remove")

    assert _page(session_factory, page_id) is not None
    assert client.get("/admin/labs").status_code == 200


def _write_routes(page_id):
    return [
        ("get", "/admin/labs", None),
        ("get", "/admin/labs/new", None),
        ("post", "/admin/labs/new", FORM),
        ("get", f"/admin/labs/{page_id}/edit", None),
        ("post", f"/admin/labs/{page_id}/edit", FORM),
        ("post", f"/admin/labs/{page_id}/delete", None),
        ("post", "/admin/labs/preview", {"body": "x"}),
    ]


@pytest.fixture
def page_id(client, boss):
    page_id = _create(client, slug="existing-page")
    client.post("/auth/logout")
    return page_id


def test_signed_out_visitor_gets_401(client, page_id, session_factory):
    for method, url, data in _write_routes(page_id):
        response = client.request(method, url, data=data)
        assert response.status_code == 401, (method, url)
    assert _page(session_factory, page_id) is not None


def test_member_gets_403(client, page_id, add_member, sign_in, session_factory):
    add_member("alice")
    sign_in("alice", github_id=42)

    for method, url, data in _write_routes(page_id):
        response = client.request(method, url, data=data)
        assert response.status_code == 403, (method, url)
    assert _page(session_factory, page_id) is not None


def test_admin_gets_through(client, page_id, sign_in):
    sign_in("boss", github_id=1)

    for method, url, data in _write_routes(page_id):
        response = client.request(method, url, data=data, follow_redirects=False)
        assert response.status_code in (200, 303), (method, url, response.status_code)


def test_account_menu_links_to_labs_admin(client, boss, add_member, sign_in):
    assert 'href="/admin/labs"' in client.get("/").text

    client.post("/auth/logout")
    add_member("alice")
    sign_in("alice", github_id=42)
    assert 'href="/admin/labs"' not in client.get("/").text

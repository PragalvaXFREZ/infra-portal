import re

import pytest
from sqlalchemy import select

from infra_portal.models import Post
from infra_portal.rendering import slugify


@pytest.fixture
def alice(add_member, sign_in):
    add_member("alice")
    sign_in("alice", github_id=42, name="Alice")


def _create(client, title="Hello Docker", body="Some **text**.", action="save"):
    response = client.post(
        "/write", data={"title": title, "body": body, "action": action}, follow_redirects=False
    )
    assert response.status_code == 303, response.text
    return int(re.search(r"/posts/(\d+)/edit", response.headers["location"]).group(1))


def test_slugify():
    assert slugify("Hello, Docker World!") == "hello-docker-world"
    assert slugify("Café über") == "cafe-uber"
    assert slugify("!!!") == "post"


def test_draft_is_visible_only_to_its_author(client, alice, app):
    _create(client)

    assert client.get("/u/alice/hello-docker").status_code == 200

    client.post("/auth/logout")
    assert client.get("/u/alice/hello-docker").status_code == 404
    assert "Hello Docker" not in client.get("/").text


def test_published_post_is_public_and_listed(client, alice):
    _create(client, action="publish")
    client.post("/auth/logout")

    page = client.get("/u/alice/hello-docker")
    assert page.status_code == 200
    assert "<strong>text</strong>" in page.text
    assert "Hello Docker" in client.get("/").text
    assert "Hello Docker" in client.get("/u/alice").text
    assert "Hello Docker" in client.get("/writing").text


def test_duplicate_titles_get_distinct_slugs(client, alice, session_factory):
    _create(client)
    _create(client)

    with session_factory() as session:
        slugs = sorted(session.scalars(select(Post.slug)))
    assert slugs == ["hello-docker", "hello-docker-2"]


def test_publish_then_unpublish(client, alice, session_factory):
    post_id = _create(client)
    client.post(f"/posts/{post_id}/edit", data={"title": "Hello Docker", "body": "x", "action": "publish"})
    with session_factory() as session:
        post = session.get(Post, post_id)
        assert post.is_published and post.published_at is not None

    client.post(f"/posts/{post_id}/edit", data={"title": "Hello Docker", "body": "x", "action": "unpublish"})
    with session_factory() as session:
        assert not session.get(Post, post_id).is_published


def test_editing_the_title_keeps_the_link(client, alice, session_factory):
    post_id = _create(client, action="publish")

    client.post(f"/posts/{post_id}/edit", data={"title": "A new title", "body": "x", "action": "save"})

    with session_factory() as session:
        post = session.get(Post, post_id)
        assert post.title == "A new title"
        assert post.slug == "hello-docker"


def test_title_is_required(client, alice):
    response = client.post("/write", data={"title": "  ", "body": "x"})

    assert response.status_code == 422
    assert "title is required" in response.text


def test_raw_html_and_script_links_are_not_rendered(client, alice):
    _create(
        client,
        body='<script>alert(1)</script>\n\n[click](javascript:alert(1))',
        action="publish",
    )

    page = client.get("/u/alice/hello-docker").text

    assert "<script>alert(1)</script>" not in page
    assert 'href="javascript:' not in page


def test_another_member_cannot_edit_or_delete(client, alice, add_member, sign_in):
    post_id = _create(client, action="publish")
    client.post("/auth/logout")
    add_member("bob")
    sign_in("bob", github_id=43)

    assert client.get(f"/posts/{post_id}/edit").status_code == 404
    assert client.post(f"/posts/{post_id}/edit", data={"title": "pwned", "body": ""}).status_code == 404
    assert client.post(f"/posts/{post_id}/delete").status_code == 404


def test_admin_can_delete_any_post(client, alice, sign_in, session_factory):
    post_id = _create(client, action="publish")
    client.post("/auth/logout")
    sign_in("boss", github_id=1)

    response = client.post(f"/posts/{post_id}/delete", follow_redirects=False)

    assert response.status_code == 303
    with session_factory() as session:
        assert session.get(Post, post_id) is None


def test_signed_out_visitor_cannot_write(client):
    assert client.get("/write").status_code == 401
    assert client.post("/write", data={"title": "x"}).status_code == 401

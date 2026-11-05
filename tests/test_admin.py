from sqlalchemy import select

from infra_portal.models import Member


def test_admin_invites_a_member(client, sign_in, session_factory):
    sign_in("boss", github_id=1)

    response = client.post("/admin/members", data={"github_login": "@NewPerson"}, follow_redirects=False)

    assert response.status_code == 303
    with session_factory() as session:
        member = session.scalar(select(Member).where(Member.github_login == "newperson"))
        assert member is not None
        assert member.role == "member"
        assert member.github_id is None


def test_invite_rejects_an_invalid_login(client, sign_in):
    sign_in("boss", github_id=1)

    response = client.post("/admin/members", data={"github_login": "not a login!"})

    assert response.status_code == 422


def test_member_cannot_open_admin_pages(client, sign_in, add_member):
    add_member("alice")
    sign_in("alice", github_id=42)

    assert client.get("/admin/members").status_code == 403
    assert client.post("/admin/members", data={"github_login": "x"}).status_code == 403


def test_signed_out_visitor_cannot_open_admin_pages(client):
    assert client.get("/admin/members").status_code == 401


def test_admin_removes_a_member_but_not_themselves(client, sign_in, add_member, session_factory):
    alice = add_member("alice")
    sign_in("boss", github_id=1)
    with session_factory() as session:
        boss = session.scalar(select(Member).where(Member.github_login == "boss"))

    assert client.post(f"/admin/members/{boss.id}/remove").status_code == 400
    assert client.post(f"/admin/members/{alice.id}/remove", follow_redirects=False).status_code == 303
    with session_factory() as session:
        assert session.get(Member, alice.id) is None


def test_member_list_escapes_html(client, sign_in, add_member):
    add_member("alice", display_name="<script>alert(1)</script>")
    sign_in("boss", github_id=1)

    page = client.get("/admin/members").text

    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page

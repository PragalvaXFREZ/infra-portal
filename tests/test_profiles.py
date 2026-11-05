def test_profile_edit_and_public_page(client, add_member, sign_in):
    add_member("alice")
    sign_in("alice", github_id=42, name="From GitHub")

    response = client.post(
        "/settings/profile",
        data={"display_name": "Alice", "bio": "I run *things*.", "website": "https://alice.dev"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    client.post("/auth/logout")

    page = client.get("/u/alice").text
    assert "Alice" in page
    assert "<em>things</em>" in page
    assert 'href="https://alice.dev"' in page


def test_github_name_does_not_overwrite_an_edited_name(client, add_member, sign_in):
    add_member("alice")
    sign_in("alice", github_id=42, name="From GitHub")
    client.post("/settings/profile", data={"display_name": "Alice"})
    client.post("/auth/logout")

    sign_in("alice", github_id=42, name="Changed On GitHub")

    assert client.get("/api/me").json()["display_name"] == "Alice"


def test_website_must_be_a_web_link(client, add_member, sign_in):
    add_member("alice")
    sign_in("alice", github_id=42)

    response = client.post("/settings/profile", data={"website": "javascript:alert(1)"})

    assert response.status_code == 422


def test_invited_but_not_joined_member_has_no_public_page(client, add_member):
    add_member("pending")

    assert client.get("/u/pending").status_code == 404
    assert "pending" not in client.get("/members").text


def test_member_directory_lists_joined_members(client, add_member, sign_in):
    add_member("alice")
    sign_in("alice", github_id=42, name="Alice")

    assert "Alice" in client.get("/members").text

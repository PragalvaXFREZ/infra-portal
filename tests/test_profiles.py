import io
import re

import pytest
from PIL import Image

from infra_portal.profiles import BadImage, make_avatar


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


def _png(width=600, height=400, color=(200, 80, 40)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), color).save(out, "PNG")
    return out.getvalue()


def test_uploaded_picture_replaces_the_github_one(client, add_member, sign_in):
    add_member("alice")
    sign_in("alice", github_id=42)

    response = client.post(
        "/settings/avatar", files={"picture": ("me.png", _png(), "image/png")}, follow_redirects=False
    )
    assert response.status_code == 303

    page = client.get("/u/alice").text
    src = re.search(r'src="(/avatars/\d+\?v=\w+)"', page).group(1)
    image = client.get(src)
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/webp"
    assert "immutable" in image.headers["cache-control"]
    with Image.open(io.BytesIO(image.content)) as stored:
        assert stored.format == "WEBP"
        assert stored.size == (256, 256)


def test_signing_in_again_keeps_the_uploaded_picture(client, add_member, sign_in):
    add_member("alice")
    sign_in("alice", github_id=42)
    client.post("/settings/avatar", files={"picture": ("me.png", _png(), "image/png")})
    client.post("/auth/logout")

    sign_in("alice", github_id=42)

    assert "/avatars/" in client.get("/u/alice").text


def test_removing_the_picture_goes_back_to_github(client, add_member, sign_in):
    add_member("alice")
    sign_in("alice", github_id=42)
    client.post("/settings/avatar", files={"picture": ("me.png", _png(), "image/png")})

    client.post("/settings/avatar/remove")

    assert "/avatars/" not in client.get("/u/alice").text


def test_a_file_that_is_not_an_image_is_refused(client, add_member, sign_in):
    add_member("alice")
    sign_in("alice", github_id=42)

    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    response = client.post("/settings/avatar", files={"picture": ("me.svg", svg, "image/svg+xml")})

    assert response.status_code == 422
    assert "/avatars/" not in client.get("/u/alice").text


def test_only_members_can_upload_a_picture(client):
    response = client.post("/settings/avatar", files={"picture": ("me.png", _png(), "image/png")})

    assert response.status_code == 401


def test_picture_follows_the_phone_rotation_tag():
    # Left half red, right half blue, tagged "rotate 90 degrees clockwise" (EXIF orientation 6).
    # Turned the right way up, red is on top.
    image = Image.new("RGB", (1600, 800), (0, 0, 255))
    image.paste((255, 0, 0), (0, 0, 800, 800))
    exif = Image.Exif()
    exif[0x0112] = 6
    out = io.BytesIO()
    image.save(out, "JPEG", exif=exif)

    with Image.open(io.BytesIO(make_avatar(out.getvalue()))) as avatar:
        top, bottom = avatar.getpixel((128, 20)), avatar.getpixel((128, 235))
    assert top[0] > 200 and top[2] < 60
    assert bottom[2] > 200 and bottom[0] < 60


def test_a_huge_png_is_refused():
    out = io.BytesIO()
    Image.new("RGB", (4000, 3000)).save(out, "PNG")

    with pytest.raises(BadImage):
        make_avatar(out.getvalue())

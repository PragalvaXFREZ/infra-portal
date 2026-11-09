"""Public member pages and profile editing."""

import hashlib
import io
import math

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import RedirectResponse
from PIL import Image, ImageOps
from sqlalchemy import select
from sqlalchemy.orm import Session, undefer

from infra_portal.auth import current_member, require_member
from infra_portal.db import get_db
from infra_portal.models import STATUS_PUBLISHED, Member, Post
from infra_portal.rendering import render_markdown
from infra_portal.templating import templates

router = APIRouter()

MAX_NAME = 100
MAX_BIO = 2000
MAX_WEBSITE = 200

# Uploaded pictures.
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_SOURCE_PIXELS = 40_000_000  # about 8000 x 5000; larger images are refused before decoding
# JPEG decodes straight to a small size. Other formats decode in full first, about 4 bytes a
# pixel, so they get a lower limit: 9 MP still takes a 4K screenshot.
MAX_FULL_DECODE_PIXELS = 9_000_000
AVATAR_SIZE = 256  # stored as a 256 x 256 WebP, shown at 88 px or smaller
UPLOAD_FORMATS = {"JPEG", "PNG", "WEBP", "GIF"}


class BadImage(ValueError):
    pass


def make_avatar(data: bytes) -> bytes:
    """Turn an uploaded image into a square WebP.

    The upload is decoded and drawn again, never stored as sent. That drops EXIF data (such as
    the GPS position a phone adds) and means only a plain image can come back out.
    """
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.format not in UPLOAD_FORMATS:
                raise BadImage("Use a JPEG, PNG, WebP or GIF image.")
            limit = MAX_SOURCE_PIXELS if source.format == "JPEG" else MAX_FULL_DECODE_PIXELS
            if source.width * source.height > limit:
                raise BadImage(f"That image is too large. Use one under {limit // 1_000_000} megapixels.")
            # Shrink first, while decoding where the format allows it (JPEG decodes at 1/2 to 1/8
            # scale), so a phone photo never sits in memory at full size. The pod has 256 MiB.
            scale = 2 * AVATAR_SIZE / min(source.size)
            if scale < 1:
                source.thumbnail((math.ceil(source.width * scale), math.ceil(source.height * scale)))
            image = ImageOps.exif_transpose(source)  # phone photos are often stored sideways
            image = ImageOps.fit(image.convert("RGB"), (AVATAR_SIZE, AVATAR_SIZE), Image.Resampling.LANCZOS)
    except BadImage:
        raise
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise BadImage("That file is not an image this site can read.") from exc
    out = io.BytesIO()
    image.save(out, "WEBP", quality=85, method=6)
    return out.getvalue()


def get_member_by_login(db: Session, login: str) -> Member:
    member = db.scalar(
        select(Member).where(Member.github_login == login.lower(), Member.github_id.is_not(None))
    )
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such member.")
    return member


def signed_in_members(db: Session) -> list[Member]:
    """Members who have signed in at least once. Pending invites stay hidden."""
    return list(
        db.scalars(
            select(Member).where(Member.github_id.is_not(None)).order_by(Member.github_login)
        ).all()
    )


@router.get("/members")
def member_directory(
    request: Request,
    member: Member | None = Depends(current_member),
    db: Session = Depends(get_db),
):
    members = signed_in_members(db)
    return templates.TemplateResponse(
        request, "members.html", {"member": member, "members": members}
    )


@router.get("/u/{login}")
def profile(
    login: str,
    request: Request,
    member: Member | None = Depends(current_member),
    db: Session = Depends(get_db),
):
    person = get_member_by_login(db, login)
    posts = db.scalars(
        select(Post)
        .where(Post.author_id == person.id, Post.status == STATUS_PUBLISHED)
        .order_by(Post.published_at.desc())
    ).all()
    return templates.TemplateResponse(
        request,
        "profile.html",
        {"member": member, "person": person, "posts": posts, "bio_html": render_markdown(person.bio or "")},
    )


@router.get("/settings/profile")
def edit_profile(request: Request, member: Member = Depends(require_member)):
    return templates.TemplateResponse(request, "profile_edit.html", {"member": member, "error": None})


@router.get("/avatars/{member_id}")
def avatar(member_id: int, db: Session = Depends(get_db)):
    person = db.scalar(select(Member).options(undefer(Member.avatar_image)).where(Member.id == member_id))
    if person is None or person.avatar_image is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No picture.")
    return Response(
        person.avatar_image,
        media_type="image/webp",
        headers={
            # The URL carries the image hash, so a new picture has a new URL and this copy can be
            # kept for good.
            "Cache-Control": "public, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/settings/avatar")
def upload_avatar(  # plain def: Pillow work runs in the thread pool, not on the event loop
    request: Request,
    picture: UploadFile = File(...),
    member: Member = Depends(require_member),
    db: Session = Depends(get_db),
):
    data = picture.file.read(MAX_UPLOAD_BYTES + 1)
    error = None
    if not data:
        error = "Choose a picture first."
    elif len(data) > MAX_UPLOAD_BYTES:
        error = "That picture is over 5 MB. Use a smaller one."
    else:
        try:
            image = make_avatar(data)
        except BadImage as exc:
            error = str(exc)
    if error:
        return templates.TemplateResponse(
            request,
            "profile_edit.html",
            {"member": member, "error": error},
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    member.avatar_image = image
    member.avatar_version = hashlib.sha256(image).hexdigest()[:12]
    db.add(member)
    db.commit()
    return RedirectResponse("/settings/profile", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/settings/avatar/remove")
def remove_avatar(member: Member = Depends(require_member), db: Session = Depends(get_db)):
    member.avatar_image = None
    member.avatar_version = None
    db.add(member)
    db.commit()
    return RedirectResponse("/settings/profile", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/settings/profile")
def save_profile(
    request: Request,
    display_name: str = Form(""),
    bio: str = Form(""),
    website: str = Form(""),
    member: Member = Depends(require_member),
    db: Session = Depends(get_db),
):
    display_name, bio, website = display_name.strip(), bio.strip(), website.strip()
    error = None
    if len(display_name) > MAX_NAME:
        error = f"Name must be at most {MAX_NAME} characters."
    elif len(bio) > MAX_BIO:
        error = f"Bio must be at most {MAX_BIO} characters."
    elif len(website) > MAX_WEBSITE:
        error = f"Website must be at most {MAX_WEBSITE} characters."
    elif website and not website.startswith(("https://", "http://")):
        # Only web links: a javascript: URL in an href would run on click.
        error = "Website must start with https:// or http://."
    if error:
        return templates.TemplateResponse(
            request,
            "profile_edit.html",
            {"member": member, "error": error},
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    member.display_name = display_name or None
    member.bio = bio or None
    member.website = website or None
    db.add(member)
    db.commit()
    return RedirectResponse(f"/u/{member.github_login}", status_code=status.HTTP_303_SEE_OTHER)

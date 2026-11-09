"""Writing, publishing and reading posts."""

from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from infra_portal.auth import current_member, require_member
from infra_portal.config import Settings, get_settings
from infra_portal.db import get_db
from infra_portal.follow import announcement, follower_count
from infra_portal.mail import Mailer, get_mailer, send_new_post
from infra_portal.models import STATUS_DRAFT, STATUS_PUBLISHED, Member, Post
from infra_portal.profiles import get_member_by_login
from infra_portal.rendering import render_markdown, slugify
from infra_portal.templating import templates

router = APIRouter()

MAX_TITLE = 200
MAX_BODY = 100_000


def _own_post(db: Session, post_id: int, member: Member) -> Post:
    post = db.get(Post, post_id)
    # The same 404 for "missing" and "not yours", so ids reveal nothing.
    if post is None or post.author_id != member.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such post.")
    return post


def _unique_slug(db: Session, author_id: int, title: str) -> str:
    base = slugify(title)
    taken = set(
        db.scalars(
            select(Post.slug).where(Post.author_id == author_id, Post.slug.startswith(base))
        )
    )
    slug, n = base, 2
    while slug in taken:
        suffix = f"-{n}"
        slug = base[: 80 - len(suffix)] + suffix
        n += 1
    return slug


def _validate(title: str, body: str) -> str | None:
    if not title:
        return "A title is required."
    if len(title) > MAX_TITLE:
        return f"Title must be at most {MAX_TITLE} characters."
    if len(body) > MAX_BODY:
        return f"The post must be at most {MAX_BODY:,} characters."
    return None


def _apply_action(post: Post, action: str) -> None:
    if action == "publish" and not post.is_published:
        post.status = STATUS_PUBLISHED
        post.published_at = datetime.now(UTC)
    elif action == "unpublish":
        post.status = STATUS_DRAFT


# The Write and Preview tabs submit these. Neither saves anything. With JavaScript the page handles
# them itself and fetches /preview; without it the form posts and the editor comes back.
VIEW_ACTIONS = ("write", "preview")


def _editor(
    request: Request,
    member: Member,
    post: Post | None,
    title: str,
    body: str,
    error: str | None,
    preview_html: str | None = None,
):
    return templates.TemplateResponse(
        request,
        "editor.html",
        {
            "member": member,
            "post": post,
            "title": title,
            "body": body,
            "error": error,
            "preview_html": preview_html,
        },
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT if error else status.HTTP_200_OK,
    )


def _view(request: Request, member: Member, post: Post | None, title: str, body: str, action: str):
    """Show the editor on the Write or Preview tab, keeping what was typed. Saves nothing."""
    if len(body) > MAX_BODY:
        return _editor(request, member, post, title, body, f"The post must be at most {MAX_BODY:,} characters.")
    preview_html = render_markdown(body) if action == "preview" else None
    return _editor(request, member, post, title, body, None, preview_html)


@router.get("/write")
def new_post_form(request: Request, member: Member = Depends(require_member)):
    return _editor(request, member, None, "", "", None)


@router.post("/write")
def create_post(
    request: Request,
    background: BackgroundTasks,
    title: str = Form(""),
    body: str = Form(""),
    action: str = Form("save"),
    member: Member = Depends(require_member),
    db: Session = Depends(get_db),
    mailer: Mailer = Depends(get_mailer),
    settings: Settings = Depends(get_settings),
):
    title = title.strip()
    if action in VIEW_ACTIONS:
        return _view(request, member, None, title, body, action)
    if error := _validate(title, body):
        return _editor(request, member, None, title, body, error)
    post = Post(author_id=member.id, title=title, body=body, slug=_unique_slug(db, member.id, title))
    _apply_action(post, action)
    db.add(post)
    news = announcement(db, settings, post, member)
    db.commit()
    if news:
        background.add_task(send_new_post, mailer, settings, news)
    return RedirectResponse(f"/posts/{post.id}/edit", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/posts/{post_id}/edit")
def edit_post_form(
    post_id: int,
    request: Request,
    member: Member = Depends(require_member),
    db: Session = Depends(get_db),
):
    post = _own_post(db, post_id, member)
    return _editor(request, member, post, post.title, post.body, None)


@router.post("/posts/{post_id}/edit")
def update_post(
    post_id: int,
    request: Request,
    background: BackgroundTasks,
    title: str = Form(""),
    body: str = Form(""),
    action: str = Form("save"),
    member: Member = Depends(require_member),
    db: Session = Depends(get_db),
    mailer: Mailer = Depends(get_mailer),
    settings: Settings = Depends(get_settings),
):
    post = _own_post(db, post_id, member)
    title = title.strip()
    if action in VIEW_ACTIONS:
        return _view(request, member, post, title, body, action)
    if error := _validate(title, body):
        return _editor(request, member, post, title, body, error)
    post.title, post.body = title, body
    _apply_action(post, action)
    news = announcement(db, settings, post, member)
    db.commit()
    if news:
        background.add_task(send_new_post, mailer, settings, news)
    return RedirectResponse(f"/posts/{post.id}/edit", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/preview")
def preview(body: str = Form(""), member: Member = Depends(require_member)) -> HTMLResponse:
    """The post body as readers will see it, for the editor's Preview tab. Saves nothing."""
    if len(body) > MAX_BODY:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The post is too long.")
    return HTMLResponse(render_markdown(body))


@router.post("/posts/{post_id}/delete")
def delete_post(
    post_id: int,
    member: Member = Depends(require_member),
    db: Session = Depends(get_db),
):
    post = db.get(Post, post_id)
    if post is None or (post.author_id != member.id and not member.is_admin):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such post.")
    author_login = post.author.github_login
    db.delete(post)
    db.commit()
    target = "/me/posts" if post.author_id == member.id else f"/u/{author_login}"
    return RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/me/posts")
def my_posts(
    request: Request,
    member: Member = Depends(require_member),
    db: Session = Depends(get_db),
):
    posts = db.scalars(
        select(Post).where(Post.author_id == member.id).order_by(Post.updated_at.desc())
    ).all()
    return templates.TemplateResponse(
        request,
        "my_posts.html",
        {"member": member, "posts": posts, "followers": follower_count(db, member.id)},
    )


@router.get("/u/{login}/{slug}")
def read_post(
    login: str,
    slug: str,
    request: Request,
    member: Member | None = Depends(current_member),
    db: Session = Depends(get_db),
):
    author = get_member_by_login(db, login)
    post = db.scalar(select(Post).where(Post.author_id == author.id, Post.slug == slug))
    is_author = member is not None and member.id == author.id
    if post is None or (not post.is_published and not is_author):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such post.")
    return templates.TemplateResponse(
        request,
        "post.html",
        {
            "member": member,
            "post": post,
            "body_html": render_markdown(post.body),
            "is_author": is_author,
            "person": author,
        },
    )


@router.get("/writing")
def writing(
    request: Request,
    member: Member | None = Depends(current_member),
    db: Session = Depends(get_db),
):
    return templates.TemplateResponse(
        request, "writing.html", {"member": member, "posts": recent_posts(db, limit=200)}
    )


def recent_posts(db: Session, limit: int = 20) -> list[Post]:
    return list(
        db.scalars(
            select(Post)
            .options(selectinload(Post.author))
            .where(Post.status == STATUS_PUBLISHED)
            .order_by(Post.published_at.desc())
            .limit(limit)
        )
    )

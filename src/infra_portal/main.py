"""HTTP entry point."""

from fastapi import Depends, FastAPI, Request, Response, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from infra_portal import (
    admin,
    admin_mail,
    auth,
    course,
    course_admin,
    follow,
    lab_admin,
    labs,
    posts,
    profiles,
    subscribe,
)
from infra_portal.auth import current_member, require_member
from infra_portal.config import get_settings
from infra_portal.db import database_is_reachable, get_db
from infra_portal.models import Member
from infra_portal.posts import recent_posts
from infra_portal.profiles import signed_in_members
from infra_portal.templating import STATIC_DIR, templates


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="infra-portal", docs_url=None, redoc_url=None)

    # Signed cookie session. SameSite=Lax keeps the cookie off cross-site POSTs,
    # which blocks cross-site form submissions, and still allows the redirect
    # back from GitHub to carry it.
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        session_cookie="portal_session",
        max_age=14 * 24 * 3600,
        same_site="lax",
        https_only=settings.cookie_secure,
    )

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(auth.router)
    app.include_router(admin.router)
    app.include_router(admin_mail.router)
    app.include_router(profiles.router)
    app.include_router(posts.router)
    app.include_router(follow.router)
    app.include_router(subscribe.router)
    app.include_router(labs.router)
    app.include_router(course.router)
    app.include_router(course_admin.router)
    app.include_router(lab_admin.router)

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> FileResponse:
        # Browsers ask for /favicon.ico even when the page names other icons.
        return FileResponse(STATIC_DIR / "icon" / "favicon-32.png", media_type="image/png")

    @app.get("/livez")
    def livez() -> dict[str, str]:
        """The process is up and can answer HTTP.

        This check must not touch the database. If it did, a database outage would
        make every API container look dead, and the orchestrator would restart them
        all without fixing anything.
        """
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz(response: Response) -> dict[str, str]:
        """The process can do useful work, which today means reaching Postgres.

        A failing readiness check takes the container out of traffic without
        restarting it.
        """
        if database_is_reachable():
            return {"status": "ok", "database": "reachable"}
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "unavailable", "database": "unreachable"}

    @app.get("/")
    def home(
        request: Request,
        member: Member | None = Depends(current_member),
        db: Session = Depends(get_db),
    ):
        return templates.TemplateResponse(
            request,
            "home.html",
            {
                "member": member,
                "labs": labs.visible_labs(db, member)[:3],
                # For a member, the card in the opening section points at their course.
                "next": course.next_step(course.load_courses(db, member)),
                "posts": recent_posts(db, limit=5),
                "people": signed_in_members(db),
            },
        )

    @app.get("/api/me")
    def me(member: Member = Depends(require_member)) -> dict[str, object]:
        return {
            "github_login": member.github_login,
            "display_name": member.display_name,
            "role": member.role,
        }

    return app


app = create_app()

"""HTTP entry point."""

from pathlib import Path

from fastapi import Depends, FastAPI, Request, Response, status
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from infra_portal import admin, auth
from infra_portal.auth import current_member, require_member
from infra_portal.config import get_settings
from infra_portal.db import database_is_reachable
from infra_portal.models import Member
from infra_portal.templating import templates


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

    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
    app.include_router(auth.router)
    app.include_router(admin.router)

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
    def home(request: Request, member: Member | None = Depends(current_member)):
        return templates.TemplateResponse(request, "home.html", {"member": member})

    @app.get("/api/me")
    def me(member: Member = Depends(require_member)) -> dict[str, object]:
        return {
            "github_login": member.github_login,
            "display_name": member.display_name,
            "role": member.role,
        }

    return app


app = create_app()

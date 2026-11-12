"""For admins: every email the site sends, shown with made-up data, and a form to send the samples.

The samples come from `mail.sample_emails`, which builds them with the same code as the real
messages, so what this page shows is what readers get.
"""

import logging

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status

from infra_portal.auth import require_admin
from infra_portal.config import Settings, get_settings
from infra_portal.follow import EMAIL_PATTERN, MAX_EMAIL
from infra_portal.mail import Mailer, get_mailer, sample_emails
from infra_portal.models import Member
from infra_portal.templating import templates

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin")


def _page(request: Request, admin: Member, settings: Settings, status_code: int = 200, **context):
    return templates.TemplateResponse(
        request,
        "admin_mail.html",
        {
            "member": admin,
            "samples": sample_emails(settings),
            "mail_enabled": settings.mail_enabled,
            **context,
        },
        status_code=status_code,
    )


@router.get("/mail")
def preview(
    request: Request,
    admin: Member = Depends(require_admin),
    settings: Settings = Depends(get_settings),
):
    return _page(request, admin, settings)


@router.post("/mail/send")
def send_samples(
    request: Request,
    to: str = Form(""),
    admin: Member = Depends(require_admin),
    mailer: Mailer = Depends(get_mailer),
    settings: Settings = Depends(get_settings),
):
    if not settings.mail_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Email is not configured.")
    to = to.strip().lower()
    if len(to) > MAX_EMAIL or not EMAIL_PATTERN.match(to):
        return _page(
            request,
            admin,
            settings,
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            to=to,
            error="That doesn't look like an email address.",
        )
    try:
        mailer.send([email for _, email in sample_emails(settings, to=to)])
    except Exception:
        log.exception("Could not send the sample emails")
        return _page(
            request,
            admin,
            settings,
            status.HTTP_502_BAD_GATEWAY,
            to=to,
            error="The emails didn't go out. The app log has the reason.",
        )
    log.info("%s sent the sample emails", admin.github_login)
    return _page(request, admin, settings, to=to, sent_to=to)

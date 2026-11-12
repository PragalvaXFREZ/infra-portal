"""Jinja2 templates. Autoescaping is on, so values from users are HTML-escaped."""

import hashlib
from functools import cache
from pathlib import Path

from fastapi.templating import Jinja2Templates

from infra_portal.config import get_settings

STATIC_DIR = Path(__file__).parent / "static"

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


@cache
def _fingerprint(path: str, mtime_ns: int) -> str:
    return hashlib.sha256((STATIC_DIR / path).read_bytes()).hexdigest()[:10]


def static_url(path: str) -> str:
    """`/static/<path>?v=<content hash>`.

    Browsers keep static files for a while. When the file changes, the hash changes, so the
    URL changes and browsers fetch the new file instead of reusing an old copy.
    """
    mtime_ns = (STATIC_DIR / path).stat().st_mtime_ns
    return f"/static/{path}?v={_fingerprint(path, mtime_ns)}"


templates.env.globals["static_url"] = static_url


def follow_enabled() -> bool:
    """Whether to show the Follow and Subscribe forms. They need the email settings."""
    return get_settings().mail_enabled


templates.env.globals["follow_enabled"] = follow_enabled

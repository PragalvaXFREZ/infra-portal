"""Jinja2 templates. Autoescaping is on, so values from users are HTML-escaped."""

from pathlib import Path

from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

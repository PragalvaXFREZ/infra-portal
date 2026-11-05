"""Markdown to HTML.

Raw HTML in a post is escaped, not rendered, so a post cannot inject scripts.
markdown-it also refuses javascript:, vbscript: and file: links.
"""

import re
import unicodedata

from markdown_it import MarkdownIt
from markupsafe import Markup

_md = MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"])


def render_markdown(text: str) -> Markup:
    # Markup tells Jinja the HTML is already safe and must not be escaped again.
    return Markup(_md.render(text or ""))


def slugify(title: str, max_length: int = 80) -> str:
    """'Hello, Docker World!' -> 'hello-docker-world'."""
    ascii_title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_title.lower()).strip("-")
    return slug[:max_length].rstrip("-") or "post"

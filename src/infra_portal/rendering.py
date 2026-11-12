"""Markdown to HTML.

Raw HTML in a post is escaped, not rendered, so a post cannot inject scripts.
markdown-it also refuses javascript:, vbscript: and file: links.
"""

import re
import unicodedata

from markdown_it import MarkdownIt
from markupsafe import Markup

from infra_portal.obsidian import callouts, wikilinks


def _parser() -> MarkdownIt:
    return MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"])


_md = _parser()
# Lab bodies get their own parser, so the Obsidian syntax in lab guides never changes how posts
# and bios render.
_lab_md = _parser().use(wikilinks).use(callouts)


def render_markdown(text: str) -> Markup:
    # Markup tells Jinja the HTML is already safe and must not be escaped again.
    return Markup(_md.render(text or ""))


def render_lab_markdown(text: str) -> Markup:
    """Like render_markdown, plus wikilinks and callouts. See obsidian.py."""
    return Markup(_lab_md.render(text or ""))


def slugify(title: str, max_length: int = 80) -> str:
    """'Hello, Docker World!' -> 'hello-docker-world'."""
    ascii_title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_title.lower()).strip("-")
    return slug[:max_length].rstrip("-") or "post"

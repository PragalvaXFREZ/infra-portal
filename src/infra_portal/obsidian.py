"""Two pieces of Obsidian syntax that lab bodies use, as markdown-it plugins.

Lab guides are first written in Obsidian, so they contain:

- Wikilinks: `[[some/path|Label]]` or `[[some/path]]`. The target is a note in the notes
  repository, not a page on this site, so only the label is shown, as plain text.
- Callouts: a blockquote whose first line is `> [!warning] Optional title`. It becomes a box
  with the title instead of a quote that shows `[!warning]`.

Neither plugin writes HTML by hand. The wikilink label becomes an ordinary text token, the
callout title an ordinary inline token, and the box class comes from a fixed list, so the normal
renderer escapes everything the author typed.
"""

import re

from markdown_it import MarkdownIt
from markdown_it.rules_core import StateCore
from markdown_it.rules_inline import StateInline
from markdown_it.token import Token

# `[[...]]` on one line, or `![[...]]`, which Obsidian uses to embed a note or a picture.
WIKILINK = re.compile(r"!?\[\[([^\[\]\n]+)\]\]")
CALLOUT = re.compile(r"^\[!([A-Za-z]+)\][+-]?(?:[ \t]+(.*))?$")
# Types with their own look. Any other type is shown like a note.
CALLOUT_TYPES = {"note", "info", "tip", "important", "warning", "caution"}


def wikilink_label(inner: str) -> str:
    """'a/b|Label' -> 'Label', 'notes/setup.md' -> 'setup', 'page#Part' -> 'page > Part'."""
    # Inside a table Obsidian writes the separator as `\\|`, so the cell does not split there.
    target, sep, label = inner.replace("\\|", "|").partition("|")
    if sep and label.strip():
        return label.strip()
    page, _, heading = target.strip().partition("#")
    page = page.rstrip("/").rsplit("/", 1)[-1].removesuffix(".md")
    return " > ".join(part.strip() for part in (page, heading) if part.strip()) or inner


def _wikilink(state: StateInline, silent: bool) -> bool:
    if state.src[state.pos] not in "![":
        return False
    match = WIKILINK.match(state.src, state.pos, state.posMax)
    if match is None:
        return False
    if not silent:
        # Pending text becomes a plain text token, which the renderer escapes.
        state.pending += wikilink_label(match.group(1))
    state.pos = match.end()
    return True


def wikilinks(md: MarkdownIt) -> None:
    # Before `link`, so `[[` is not read as the start of a Markdown link. Code spans and fenced
    # blocks are parsed by other rules first, so `[[ -f x ]]` in shell code stays as it is.
    md.inline.ruler.before("link", "wikilink", _wikilink)


def _matching_close(tokens: list[Token], start: int) -> Token:
    depth = 0
    for token in tokens[start:]:
        if token.type == "blockquote_open":
            depth += 1
        elif token.type == "blockquote_close":
            depth -= 1
            if depth == 0:
                return token
    raise ValueError("blockquote without a close")  # markdown-it always closes them


def _callouts(state: StateCore) -> None:
    tokens = state.tokens
    i = 0
    while i < len(tokens):
        token = tokens[i]
        is_quote = (
            token.type == "blockquote_open"
            and i + 3 < len(tokens)
            and tokens[i + 1].type == "paragraph_open"
            and tokens[i + 2].type == "inline"
        )
        if is_quote:
            inline = tokens[i + 2]
            first, _, rest = inline.content.partition("\n")
            match = CALLOUT.match(first.strip())
            if match:
                kind = match.group(1).lower()
                title = (match.group(2) or "").strip() or kind.capitalize()
                close = _matching_close(tokens, i)
                token.tag = close.tag = "div"
                token.attrSet("class", f"callout callout-{kind if kind in CALLOUT_TYPES else 'note'}")
                token.attrSet("role", "note")
                heading = _title_tokens(title, token.level + 1)
                if rest.strip():
                    inline.content = rest
                    tokens[i + 1 : i + 1] = heading
                else:
                    # The marker line was the whole paragraph: the title takes its place.
                    tokens[i + 1 : i + 4] = heading
        i += 1


def _title_tokens(title: str, level: int) -> list[Token]:
    opening = Token("callout_title_open", "p", 1, attrs={"class": "callout-title"}, block=True, level=level)
    # Parsed by the normal inline rule, which runs after this one, so the title is escaped.
    text = Token("inline", "", 0, content=title, children=[], level=level + 1)
    closing = Token("callout_title_close", "p", -1, block=True, level=level)
    return [opening, text, closing]


def callouts(md: MarkdownIt) -> None:
    # After the block rule has found the blockquotes, before the inline rule parses the text.
    md.core.ruler.after("block", "callouts", _callouts)

"""Wikilinks and callouts in lab bodies, and that they open no way to inject HTML."""

from datetime import date

import pytest

from infra_portal.models import LabPage
from infra_portal.obsidian import wikilink_label
from infra_portal.rendering import render_lab_markdown, render_markdown


@pytest.mark.parametrize(
    ("inner", "label"),
    [
        ("some/path|Label", "Label"),
        ("some/path", "path"),
        ("notes/made-up.md", "made-up"),
        ("page#Part", "page > Part"),
        ("a/b\\|Label", "Label"),
        ("a/b| ", "b"),
    ],
)
def test_wikilink_label(inner, label):
    assert wikilink_label(inner) == label


def test_wikilinks_render_as_plain_text():
    html = render_lab_markdown("See [[some/path|the label]] and [[other/page]].")

    assert html == "<p>See the label and page.</p>\n"


def test_wikilink_in_a_table_cell():
    html = render_lab_markdown("| A | B |\n| --- | --- |\n| x | [[a/b\\|In a cell]] |\n")

    assert "<td>In a cell</td>" in html
    assert "[[" not in html


def test_code_keeps_double_brackets():
    html = render_lab_markdown("Run `[[ -f x ]]`.\n\n```bash\nif [[ -f x ]]; then echo y; fi\n```\n")

    assert "<code>[[ -f x ]]</code>" in html
    assert "if [[ -f x ]]; then echo y; fi" in html
    assert '<pre><code class="language-bash">' in html


def test_callout_with_a_title_and_body():
    html = render_lab_markdown("> [!warning] Made-up title\n> Made-up **body**.\n")

    assert html == (
        '<div class="callout callout-warning" role="note">\n'
        '<p class="callout-title">Made-up title</p>\n'
        "<p>Made-up <strong>body</strong>.</p>\n"
        "</div>\n"
    )


@pytest.mark.parametrize("kind", ["note", "tip", "info", "important", "caution", "warning"])
def test_each_callout_type_gets_its_class_and_a_default_title(kind):
    html = render_lab_markdown(f"> [!{kind.upper()}]\n> Text.\n")

    assert f'class="callout callout-{kind}"' in html
    assert f'<p class="callout-title">{kind.capitalize()}</p>' in html
    assert "[!" not in html
    assert "<blockquote>" not in html


def test_unknown_callout_type_looks_like_a_note():
    html = render_lab_markdown('> [!made" onclick="x] Title\n')
    assert "callout" not in html  # not a valid type name, so it stays a quote

    html = render_lab_markdown("> [!madeup] Title\n")
    assert 'class="callout callout-note"' in html


def test_a_plain_quote_stays_a_quote():
    assert render_lab_markdown("> Just a quote.\n") == "<blockquote>\n<p>Just a quote.</p>\n</blockquote>\n"


def test_callout_with_a_code_block_and_a_nested_callout():
    html = render_lab_markdown(
        "> [!note] Outer\n> ```bash\n> echo hi\n> ```\n>\n> > [!tip] Inner\n> > Deep.\n"
    )

    assert html.count('role="note"') == 2
    assert "<blockquote>" not in html
    assert "echo hi" in html


@pytest.mark.parametrize(
    "body",
    [
        "<script>alert(1)</script>",
        "> [!warning] <script>alert(1)</script>\n> body",
        "> [!note]\n> <script>alert(1)</script>",
        "[[path|<script>alert(1)</script>]]",
        # No label: the last path part is shown, here everything, since there is no slash.
        "[[<script>alert(1)]]",
        "| a |\n| --- |\n| [[x\\|<script>alert(1)</script>]] |",
    ],
)
def test_html_stays_escaped(body):
    html = render_lab_markdown(body)

    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_posts_and_bios_are_unchanged():
    html = render_markdown("> [!note] Title\n\n[[a|b]]")

    assert "<blockquote>" in html
    assert "[!note] Title" in html
    assert "[[a|b]]" in html


def test_lab_page_renders_and_escapes(client, session_factory):
    with session_factory() as session:
        session.add(
            LabPage(
                slug="made-up-render",
                title="Made-up render",
                kind="lab",
                date=date(2099, 1, 1),
                summary="Made-up.",
                visibility="public",
                body=(
                    "> [!caution] <script>alert(1)</script>\n> Careful.\n\n"
                    "[[x/y|<script>alert(2)</script>]]\n\n<script>alert(3)</script>"
                ),
            )
        )
        session.commit()

    page = client.get("/labs/made-up-render").text

    assert 'class="callout callout-caution"' in page
    assert "<script>alert" not in page
    assert page.count("&lt;script&gt;alert") == 3

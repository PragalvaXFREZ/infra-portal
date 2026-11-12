import pytest

from infra_portal.labs import all_labs, parse_lab

GOOD = """---
title: A lab
kind: lab
date: 2026-10-09
summary: One sentence.
link: https://example.com
---
Body with **bold**.
"""


def test_parse_lab_reads_header_and_body():
    lab = parse_lab("a-lab", GOOD)
    assert lab.title == "A lab"
    assert lab.kind == "lab"
    assert lab.date.isoformat() == "2026-10-09"
    assert lab.link == "https://example.com"
    assert lab.body == "Body with **bold**."
    assert lab.url == "/labs/a-lab"


def test_parse_lab_accepts_a_learning_session():
    lab = parse_lab("a-session", GOOD.replace("kind: lab", "kind: session"))
    assert lab.kind == "session"
    assert lab.kind_label == "Learning session"
    assert lab.visibility == "public"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("title: x\n", "must start with"),
        ("---\ntitle: x\n", "no closing"),
        ("---\ntitle x\n---\n", "no colon"),
        ("---\ntitle: x\nkind: lab\ndate: 2026-10-09\n---\n", "missing summary"),
        ("---\ntitle: x\nkind: talk\ndate: 2026-10-09\nsummary: s\n---\n", "kind must be"),
    ],
)
def test_parse_lab_rejects_bad_files(text, message):
    with pytest.raises(ValueError, match=message):
        parse_lab("bad", text)


def test_shipped_labs_parse_and_are_newest_first():
    labs = all_labs()
    assert labs
    assert [lab.date for lab in labs] == sorted((lab.date for lab in labs), reverse=True)


def test_lab_pages(client):
    lab = all_labs()[0]
    listing = client.get("/labs")
    assert listing.status_code == 200
    assert lab.title in listing.text
    page = client.get(lab.url)
    assert page.status_code == 200
    assert lab.summary in page.text
    assert client.get("/labs/no-such-lab").status_code == 404


def test_home_shows_labs(client):
    assert all_labs()[0].title in client.get("/").text

from infra_portal.course_import import PageDraft, Plan, StageDraft, plan_import

# A made-up baking course, in the same shape as the real notes.
STAGE = """---
title: "Baking course, stage 2: bread at home"
description: "Plan for the bread stage: four chunks and two builds."
tags:
  - baking
  - bread
up: "[[courses/baking/index]]"
---

A note for the mentor, before the chunks. It must not reach the site.

## Before you send this

- **Secret:** the oven at the club runs hot.

## Build outlines

### Build 1. A first loaf

OUTLINE-ONE: the student bakes one loaf and weighs it.

### Build 2. Rolls for a crowd (not written yet)

OUTLINE-TWO: twenty rolls that all rise the same.

---

## Chunk 1. Flour and water

What flour is, and why water
changes it.

**Watch (12 min)**

- [Flour](https://example.com/flour)

**Feeds:** build 1.

---

## Chunk 2. Yeast

Yeast is alive. This chunk is about keeping it that way.

**Watch (20 min)**

```text
## not a heading, it is in a code block
---
still in the code block
```

**Feeds:** build 2.

---

## Chunk 3. Kneading

Your hands do the work here.

**Feeds:** build 1.

---

## Chunk 4. Ovens

Every oven lies about its heat.

---

## Later stages

| Stage | Topic |
|---|---|
| 1 | Measuring. Done. |
| 2 | Bread. This note. |
| 3 | Cakes |
"""

BUILD = """---
title: "Stage 2, build 1: a first loaf"
description: "Bake one loaf, weigh it, and write down what went wrong."
tags:
  - baking
---

## Part 1. Mix

Mix [[courses/baking/stage-2|the dough]].
"""

WARMUP = """---
title: "Stage 2, chunk 1: warm-up"
---

Weigh 100 g of flour three times.
"""


def page(plan: Plan, slug: str) -> PageDraft:
    return next(p for p in plan.pages if p.slug == slug)


def full_plan() -> Plan:
    return plan_import(
        [
            ("stage-2-chunk-1.md", WARMUP),
            ("stage-2-bread.md", STAGE),
            ("stage-2-build-1.md", BUILD),
        ]
    )


def test_header_keeps_the_colon_in_a_quoted_title_and_skips_lists():
    plan = plan_import([("stage-2-bread.md", STAGE)])
    assert StageDraft(2, "bread at home") in plan.stages
    build = plan_import([("b.md", BUILD)]).pages[0]
    assert build.title == "A first loaf"
    assert build.summary == "Bake one loaf, weigh it, and write down what went wrong."


def test_a_file_with_no_header_gives_nothing():
    plan = plan_import([("loose.md", "## Chunk 1. Flour\n\nSome text.\n")])
    assert plan == Plan(stages=(), pages=(), refused=(), unused=("loose.md",))


def test_a_header_with_no_closing_line_is_read_as_no_header():
    text = '---\ntitle: "Stage 2, build 1: a loaf"\n\nBody.\n'
    assert plan_import([("open.md", text)]).unused == ("open.md",)


def test_stage_note_gives_only_its_chunks_and_build_titles():
    plan = plan_import([("stage-2-bread.md", STAGE)])
    assert [p.slug for p in plan.pages if p.kind == "chunk"] == [
        "stage-2-chunk-1",
        "stage-2-chunk-3",
        "stage-2-chunk-2",
        "stage-2-chunk-4",
    ]
    for p in plan.pages:
        for text in ("OUTLINE-ONE", "OUTLINE-TWO", "mentor", "Secret", "Later stages", "| Stage"):
            assert text not in p.body
            assert text not in p.summary


def test_chunk_fields():
    chunk = page(plan_import([("s.md", STAGE)]), "stage-2-chunk-1")
    assert (chunk.stage, chunk.kind, chunk.number) == (2, "chunk", 1)
    assert chunk.title == "Flour and water"
    assert chunk.summary == "What flour is, and why water changes it."
    assert chunk.minutes == 12
    assert chunk.body.startswith("What flour is")
    assert chunk.body.endswith("**Feeds:** build 1.")


def test_a_chunk_with_no_watch_line_has_no_minutes():
    assert page(plan_import([("s.md", STAGE)]), "stage-2-chunk-3").minutes is None


def test_code_block_does_not_split_or_end_a_chunk():
    body = page(plan_import([("s.md", STAGE)]), "stage-2-chunk-2").body
    assert "## not a heading, it is in a code block\n---\nstill in the code block\n```" in body
    assert body.endswith("**Feeds:** build 2.")


def test_trailing_rule_lines_and_blank_lines_are_stripped():
    plan = plan_import([("s.md", STAGE)])
    assert page(plan, "stage-2-chunk-4").body == "Every oven lies about its heat."
    for p in plan.pages:
        assert not p.body.endswith("---")
        assert p.body == p.body.strip()


def test_a_rule_line_at_the_end_of_an_open_code_block_is_kept():
    text = '---\ntitle: "C, stage 1: x"\n---\n## Chunk 1. Code\n\nIntro.\n\n```\nline\n---\n'
    body = plan_import([("s.md", text)]).pages[0].body
    assert body.endswith("```\nline\n---")


def test_summary_is_cut_at_a_word_with_an_ellipsis():
    long = "word " * 100
    text = f'---\ntitle: "C, stage 1: x"\n---\n## Chunk 1. Long\n\n{long}\n\nSecond paragraph.\n'
    summary = plan_import([("s.md", text)]).pages[0].summary
    assert len(summary) <= 300
    assert summary.endswith("word…")
    assert "Second" not in summary


def test_a_summary_of_exactly_300_characters_is_not_cut():
    first = "a" * 300
    text = f'---\ntitle: "C, stage 1: x"\n---\n## Chunk 1. Long\n\n{first}\n'
    assert plan_import([("s.md", text)]).pages[0].summary == first


def test_build_note_makes_the_build_page():
    built = page(full_plan(), "stage-2-build-1")
    assert built.kind == "build"
    assert built.title == "A first loaf"
    assert built.minutes is None
    # The body is kept as it is, wikilinks too. The site renders them.
    assert built.body == "## Part 1. Mix\n\nMix [[courses/baking/stage-2|the dough]]."


def test_build_known_only_by_title_has_an_empty_page():
    plan = plan_import([("stage-2-bread.md", STAGE)])
    for number, title in [(1, "A first loaf"), (2, "Rolls for a crowd")]:
        built = page(plan, f"stage-2-build-{number}")
        assert (built.title, built.summary, built.minutes, built.body) == (title, "", None, "")


def test_build_note_replaces_the_title_only_page():
    plan = full_plan()
    assert [p.slug for p in plan.pages].count("stage-2-build-1") == 1
    assert page(plan, "stage-2-build-1").body.startswith("## Part 1. Mix")
    assert page(plan, "stage-2-build-2").body == ""


def test_build_note_without_a_description_has_an_empty_summary():
    text = '---\ntitle: "Stage 4, build 2: icing"\n---\nPipe the icing.\n'
    built = plan_import([("b.md", text)]).pages[0]
    assert (built.slug, built.title, built.summary, built.body) == (
        "stage-4-build-2",
        "Icing",
        "",
        "Pipe the icing.",
    )


def test_warmup_is_added_to_its_chunk():
    plan = full_plan()
    body = page(plan, "stage-2-chunk-1").body
    assert body.endswith("**Feeds:** build 1.\n\n## Warm-up steps\n\nWeigh 100 g of flour three times.")
    assert plan.unused == ()
    assert plan.refused == ()


def test_warmup_for_a_missing_chunk_is_unused():
    orphan = WARMUP.replace("chunk 1", "chunk 9")
    plan = plan_import([("stage-2-bread.md", STAGE), ("stage-2-chunk-9.md", orphan)])
    assert plan.unused == ("stage-2-chunk-9.md",)
    assert all("Weigh 100 g" not in p.body for p in plan.pages)


def test_note_kinds_are_matched_without_regard_to_case():
    text = '---\ntitle: "STAGE 3, BUILD 1: sponge"\n---\nWhisk.\n'
    assert plan_import([("b.md", text)]).pages[0].slug == "stage-3-build-1"


def test_mentor_file_name_is_refused():
    plan = plan_import([("stage-2-build-1-MENTOR.md", BUILD)])
    assert plan.pages == ()
    assert plan.stages == ()
    assert [r.name for r in plan.refused] == ["stage-2-build-1-MENTOR.md"]
    assert "-mentor.md" in plan.refused[0].reason


def test_draft_header_is_refused_before_anything_is_read():
    for value in ("true", "True", '"true"', "'TRUE'"):
        text = STAGE.replace("tags:", f"draft: {value}\ntags:", 1)
        plan = plan_import([("stage-2-bread.md", text)])
        assert (plan.pages, plan.stages, plan.unused) == ((), (), ())
        assert "draft: true" in plan.refused[0].reason
    kept = plan_import([("s.md", STAGE.replace("tags:", "draft: false\ntags:", 1))])
    assert kept.pages and kept.refused == ()


def test_stage_table_drops_the_two_short_endings():
    plan = plan_import([("index.md", STAGE.replace("stage 2: bread at home", "an index"))])
    assert plan.stages == (StageDraft(1, "Measuring"), StageDraft(2, "Bread"), StageDraft(3, "Cakes"))
    assert plan.pages == ()
    assert plan.unused == ()


def test_stage_table_keeps_other_endings():
    table = "| Stage | Topic |\n|---|---|\n| 5 | Pies. Soon. |\n| 6 | Done. |\n| x | Not a stage |\n"
    assert plan_import([("index.md", table)]).stages == (StageDraft(5, "Pies. Soon."), StageDraft(6, "Done."))


def test_stage_note_title_wins_over_a_table():
    plan = plan_import([("stage-2-bread.md", STAGE)])
    assert plan.stages == (
        StageDraft(1, "Measuring"),
        StageDraft(2, "bread at home"),
        StageDraft(3, "Cakes"),
    )


def test_first_table_in_file_name_order_wins():
    first = "| Stage | Topic |\n|---|---|\n| 7 | Tarts |\n"
    second = "| Stage | Topic |\n|---|---|\n| 7 | Biscuits |\n| 8 | Scones |\n"
    plan = plan_import([("b.md", second), ("a.md", first)])
    assert plan.stages == (StageDraft(7, "Tarts"), StageDraft(8, "Scones"))


def test_a_stage_with_pages_but_no_row_gets_a_plain_title():
    plan = plan_import([("b.md", BUILD)])
    assert plan.stages == (StageDraft(2, "Stage 2"),)


def test_positions_put_feeding_chunks_before_their_build():
    plan = full_plan()
    assert [(p.slug, p.position) for p in plan.pages] == [
        ("stage-2-chunk-1", 1),
        ("stage-2-chunk-3", 2),
        ("stage-2-build-1", 3),
        ("stage-2-chunk-2", 4),
        ("stage-2-build-2", 5),
        ("stage-2-chunk-4", 6),
    ]


def test_chunk_that_feeds_a_missing_build_goes_last():
    text = STAGE.replace("**Feeds:** build 1.", "**Feeds:** build 9.")
    plan = plan_import([("s.md", text)])
    assert [p.slug for p in plan.pages] == [
        "stage-2-build-1",
        "stage-2-chunk-2",
        "stage-2-build-2",
        "stage-2-chunk-1",
        "stage-2-chunk-3",
        "stage-2-chunk-4",
    ]


def test_positions_count_from_one_in_each_stage():
    other = '---\ntitle: "Stage 3, build 1: sponge"\n---\nWhisk.\n'
    plan = plan_import([("s.md", STAGE), ("t.md", other)])
    assert [(p.stage, p.position) for p in plan.pages][-1] == (3, 1)


def test_repeated_page_refuses_the_later_file():
    plan = plan_import([("b-copy.md", BUILD), ("a.md", BUILD)])
    assert [p.slug for p in plan.pages] == ["stage-2-build-1"]
    assert [r.name for r in plan.refused] == ["b-copy.md"]
    assert "stage-2-build-1" in plan.refused[0].reason
    assert "a.md" in plan.refused[0].reason


def test_refused_repeat_gives_nothing_at_all():
    copy = STAGE.replace("bread at home", "a copy").replace("| 3 | Cakes |", "| 4 | Pies |")
    plan = plan_import([("stage-2-bread.md", STAGE), ("stage-2-copy.md", copy)])
    assert [r.name for r in plan.refused] == ["stage-2-copy.md"]
    assert StageDraft(2, "bread at home") in plan.stages
    assert all(s.number != 4 for s in plan.stages)


def test_a_title_only_build_does_not_count_as_a_repeat():
    # The build note sorts first and gives stage-2-build-1. The stage note names the same build.
    plan = plan_import([("stage-2-plan.md", STAGE), ("stage-2-build-1.md", BUILD)])
    assert plan.refused == ()
    assert page(plan, "stage-2-build-1").body.startswith("## Part 1. Mix")


def test_each_file_is_used_refused_or_unused():
    files = [
        ("a.md", STAGE),
        ("b.md", BUILD),
        ("c-mentor.md", BUILD),
        ("d.md", "Just text."),
        ("e.md", WARMUP),
        ("f.md", BUILD),
    ]
    plan = plan_import(files)
    assert [r.name for r in plan.refused] == ["c-mentor.md", "f.md"]
    assert plan.unused == ("d.md",)


def test_title_is_cut_to_200_characters():
    text = f'---\ntitle: "Stage 1, build 1: {"x" * 300}"\n---\nBody.\n'
    assert len(plan_import([("b.md", text)]).pages[0].title) == 200
    stage = f'---\ntitle: "C, stage 1: {"y" * 300}"\n---\n### Build 1. {"z" * 300}\n'
    plan = plan_import([("s.md", stage)])
    assert len(plan.stages[0].title) == 200
    assert len(plan.pages[0].title) == 200


def test_slug_stays_short_and_long_numbers_are_not_read():
    big = "9" * 9
    text = f'---\ntitle: "Stage {big}, build {big}: x"\n---\nBody.\n'
    slug = plan_import([("b.md", text)]).pages[0].slug
    assert slug == f"stage-{big}-build-{big}"
    assert len(slug) <= 80
    too_big = f'---\ntitle: "C, stage 1: x"\n---\n## Chunk {"9" * 90}. Big\n\nText.\n'
    assert plan_import([("s.md", too_big)]).pages == ()


def test_a_file_with_the_same_chunk_twice_is_refused():
    text = '---\ntitle: "C, stage 1: x"\n---\n## Chunk 1. A\n\nOne.\n\n## Chunk 1. B\n\nTwo.\n'
    plan = plan_import([("s.md", text)])
    assert (plan.pages, plan.stages) == ((), ())
    assert "stage-1-chunk-1" in plan.refused[0].reason


def test_a_second_warmup_for_the_same_chunk_is_refused():
    plan = plan_import([("s.md", STAGE), ("w1.md", WARMUP), ("w2.md", WARMUP.replace("100 g", "200 g"))])
    assert [r.name for r in plan.refused] == ["w2.md"]
    body = page(plan, "stage-2-chunk-1").body
    assert "100 g" in body
    assert "200 g" not in body

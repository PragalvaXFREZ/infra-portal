"""Turn the notes of a course into plain data, ready to be stored.

A course is a list of numbered stages. Each stage has chunks (one short sitting of videos) and
builds (hands-on work). The course is written as Obsidian notes in a private notes repository.
An admin uploads those files, and this module reads their text. It does no I/O and touches no
database: text goes in, dataclasses come out, and other code decides what to store.

Every note starts with a small header between two `---` lines. Only `title`, `description` and
`draft` matter here. The title says what kind of note it is:

- `Some course, stage 2: Bread` is a stage note. Its `## Chunk N. Title` sections become chunk
  pages, and its `### Build N. Title` headings name the builds of the stage.
- `Stage 2, build 1: a first loaf` is a build note. Its whole body is the guide for that build.
- `Stage 2, chunk 3: warm-up` is a warm-up note. Its body is added to the end of chunk 3.

The notes also hold text meant only for the mentor, so the reader works from an allow-list. From
a stage note it takes the chunk sections and the build titles, nothing else. Answer keys (a
`-mentor.md` file name or `draft: true` in the header) are refused before anything is read.

Any note may also hold a table whose header starts with `Stage | Topic`. Its rows name stages.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field, replace

TITLE_LIMIT = 200
SUMMARY_LIMIT = 300

# Numbers have at most nine digits. They then fit a database integer column, and the longest
# slug, "stage-999999999-build-999999999", stays far under the 80 character limit. A heading
# with a longer number is not read as a chunk or a build at all, rather than being cut down to
# a number that could clash with a real one.
NUMBER = r"(\d{1,9})(?!\d)"

STAGE_TITLE = re.compile(rf"\bstage\s+{NUMBER}\s*:\s*(.+)$", re.IGNORECASE)
BUILD_TITLE = re.compile(rf"^stage\s+{NUMBER}\s*,\s*build\s+{NUMBER}\s*:\s*(.+)$", re.IGNORECASE)
WARMUP_TITLE = re.compile(rf"^stage\s+{NUMBER}\s*,\s*chunk\s+{NUMBER}\s*:\s*warm-up$", re.IGNORECASE)

CHUNK_HEADING = re.compile(rf"^## +chunk\s+{NUMBER}\.(.*)$", re.IGNORECASE)
BUILD_HEADING = re.compile(rf"^### +build\s+{NUMBER}\.(.*)$", re.IGNORECASE)
NOT_WRITTEN = re.compile(r"\s*\(not written yet\)\s*$", re.IGNORECASE)
WATCH = re.compile(r"\*\*Watch\s*\(\s*(\d{1,9})\s*min\s*\)\*\*", re.IGNORECASE)
FEEDS = re.compile(rf"\*\*Feeds:\*\*\s*build\s+{NUMBER}", re.IGNORECASE)
RULE = re.compile(r"^ {0,3}-{3,}\s*$")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
# A stage table row may end with a short note to the mentor, as in "Bread. Done.".
# Where a chunk section ends: any first or second level heading, however it is indented.
SECTION_END = re.compile(r"^ {0,3}#{1,2}(?:[ \t]|$)")
# Text the notes app hides from its reader: `%% ... %%` and `<!-- ... -->`. The first group is
# an inline code span, which is kept as it is.
HIDDEN = re.compile(r"((`+)[^\n]*?\2)|%%.*?%%|<!--.*?-->", re.DOTALL)
# What a warm-up note is put under, at the end of its chunk.
WARM_UP_HEADING = "\n\n## Warm-up steps\n\n"
TABLE_ENDING = re.compile(r"^(.*\S)\.\s+(?:done|this note)\.$", re.IGNORECASE)


@dataclass(frozen=True)
class StageDraft:
    number: int
    title: str


@dataclass(frozen=True)
class PageDraft:
    stage: int
    kind: str  # "chunk" or "build"
    number: int
    slug: str
    title: str
    summary: str
    minutes: int | None
    position: int  # order inside the stage
    body: str  # "" for a build known only by its title


@dataclass(frozen=True)
class Refused:
    name: str  # file name
    reason: str  # one plain sentence for the admin


@dataclass(frozen=True)
class Plan:
    stages: tuple[StageDraft, ...]  # sorted by number, one per number
    pages: tuple[PageDraft, ...]  # sorted by (stage, position)
    refused: tuple[Refused, ...]
    unused: tuple[str, ...]  # files that gave nothing and were not refused


@dataclass(frozen=True)
class _Chunk:
    number: int
    title: str
    summary: str
    minutes: int | None
    feeds: int | None
    body: str


@dataclass(frozen=True)
class _Build:
    number: int
    title: str
    summary: str
    body: str


@dataclass
class _Note:
    """What one file gave, before the files are put together."""

    stage: int | None = None
    stage_title: str | None = None  # only a stage note sets this
    chunks: list[_Chunk] = field(default_factory=list)
    build_titles: dict[int, str] = field(default_factory=dict)
    build: _Build | None = None
    warmup: tuple[int, str] | None = None  # (chunk number, body)
    table_rows: list[StageDraft] = field(default_factory=list)

    def slugs(self) -> list[str]:
        """The full pages this file makes. Builds known only by their title do not count."""
        found = [_slug(self.stage, "chunk", chunk.number) for chunk in self.chunks]
        if self.build is not None:
            found.append(_slug(self.stage, "build", self.build.number))
        return found


def _slug(stage: int | None, kind: str, number: int) -> str:
    return f"stage-{stage}-{kind}-{number}"


def _cut_title(text: str) -> str:
    return text.strip()[:TITLE_LIMIT].rstrip()


def _cut_summary(text: str) -> str:
    """One line of at most 300 characters, cut between words with an ellipsis if too long."""
    text = " ".join(text.split())
    if len(text) <= SUMMARY_LIMIT:
        return text
    # Leave room for the ellipsis. Cut at the last space, unless the cut already falls on one.
    cut = text[: SUMMARY_LIMIT - 1]
    if text[SUMMARY_LIMIT - 1] != " " and " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:") + "…"


def _split_header(text: str) -> tuple[dict[str, str], str]:
    """Read the header into a dict and return it with the body.

    This is not a YAML reader, on purpose: only a few plain keys matter. One pair of quotes
    around a value is removed, so `title: "Stage 2, build 1: a loaf"` keeps its colon. List
    items on the lines below a key (`  - x`) are skipped. A file with no header, or a header
    with no closing line, gives an empty dict and the whole text as the body.
    """
    # Some editors start a file with an invisible byte order mark.
    lines = text.removeprefix("\ufeff").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text.strip()
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return {}, text.strip()

    fields: dict[str, str] = {}
    for line in lines[1:end]:
        if not line.strip() or line[0] in " \t-#":
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        fields[key.strip().lower()] = value
    return fields, "\n".join(lines[end + 1 :]).strip()


def _fence_flags(lines: list[str]) -> list[bool]:
    """For each line, True if it is inside a fenced code block or is one of its fence lines.

    Inside a fence, `## ` is code and `---` is text, so neither may split or end a section.
    """
    flags = []
    opener: str | None = None
    for line in lines:
        match = FENCE.match(line)
        if opener is None:
            # A line such as "```ls``` lists files" is inline code, not the start of a block:
            # the text after an opening run of backticks may not hold another backtick.
            if match and not (match.group(1)[0] == "`" and "`" in line[match.end() :]):
                opener = match.group(1)
            flags.append(opener is not None)
        else:
            flags.append(True)
            # A fence closes with the same character, at least as many times, and nothing after.
            if match and match.group(1)[0] == opener[0] and len(match.group(1)) >= len(opener):
                if not line.strip().strip(opener[0]):
                    opener = None
    return flags


def _drop_hidden(body: str) -> str:
    """Remove comments the notes app does not show: `%% ... %%` and `<!-- ... -->`.

    The author cannot see them on the rendered note, so they may hold remarks for the mentor.
    Code is left alone, both fenced blocks and inline spans, where `%%` is ordinary text.
    """
    lines = body.splitlines()
    fenced = _fence_flags(lines)
    out: list[str] = []
    run: list[str] = []

    def flush() -> None:
        if run:
            out.append(HIDDEN.sub(lambda m: m.group(1) or "", "\n".join(run)))
            run.clear()

    for line, inside in zip(lines, fenced, strict=True):
        if inside:
            flush()
            out.append(line)
        else:
            run.append(line)
    flush()
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def _strip_ends(lines: list[str], fenced: list[bool]) -> str:
    """Drop blank lines at both ends and rule lines at the end, but not rules inside a fence."""
    start, end = 0, len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start:
        line = lines[end - 1]
        if line.strip() and not (RULE.match(line) and not fenced[end - 1]):
            break
        end -= 1
    return "\n".join(lines[start:end])


def _first_paragraph(body: str) -> str:
    paragraph = []
    for line in body.splitlines():
        if line.strip():
            paragraph.append(line.strip())
        elif paragraph:
            break
    return " ".join(paragraph)


def _read_stage_body(body: str) -> tuple[list[_Chunk], dict[int, str]]:
    """Take the chunk sections and the build titles. Every other part of the body is dropped."""
    lines = body.splitlines()
    fenced = _fence_flags(lines)
    chunks: list[_Chunk] = []
    build_titles: dict[int, str] = {}

    # Where a section starts: a first or second level heading, or the heading of a build's
    # outline. A chunk runs from its own heading to the next of these, so text for the mentor
    # that follows the last chunk under any such heading is not taken along.
    starts = [
        i
        for i, line in enumerate(lines)
        if not fenced[i] and (SECTION_END.match(line) or BUILD_HEADING.match(line.rstrip()))
    ]
    for i, start in enumerate(starts):
        match = CHUNK_HEADING.match(lines[start].rstrip())
        if match is None:
            continue
        number = int(match.group(1))
        end = starts[i + 1] if i + 1 < len(starts) else len(lines)
        section = lines[start + 1 : end]
        section_fenced = fenced[start + 1 : end]
        text = _strip_ends(section, section_fenced)
        plain = [line for line, inside in zip(section, section_fenced, strict=True) if not inside]
        minutes = next((int(m.group(1)) for line in plain if (m := WATCH.search(line))), None)
        feeds = next((int(m.group(1)) for line in plain if (m := FEEDS.search(line))), None)
        chunks.append(
            _Chunk(
                number=number,
                title=_cut_title(match.group(2)) or f"Chunk {number}",
                summary=_cut_summary(_first_paragraph(text)),
                minutes=minutes,
                feeds=feeds,
                body=text,
            )
        )

    # Only the title of a build is kept. The paragraph under it is an outline for the mentor.
    for line, inside in zip(lines, fenced, strict=True):
        match = BUILD_HEADING.match(line.rstrip())
        if match and not inside:
            number = int(match.group(1))
            title = _cut_title(NOT_WRITTEN.sub("", match.group(2)))
            build_titles.setdefault(number, title or f"Build {number}")
    return chunks, build_titles


def _read_tables(body: str) -> list[StageDraft]:
    """Rows of every table whose header row starts with the cells Stage and Topic."""
    lines = body.splitlines()
    fenced = _fence_flags(lines)
    rows: list[StageDraft] = []
    in_table = False
    for line, inside in zip(lines, fenced, strict=True):
        stripped = line.strip()
        if inside or not stripped.startswith("|"):
            in_table = False
            continue
        # A cell may hold `\|`, as Obsidian writes a wikilink label inside a table.
        cells = [cell.strip() for cell in re.split(r"(?<!\\)\|", stripped.strip("|"))]
        if not in_table:
            in_table = len(cells) >= 2 and [c.lower() for c in cells[:2]] == ["stage", "topic"]
            continue
        if len(cells) < 2 or not re.fullmatch(r"\d{1,9}", cells[0]):
            continue  # the |---|---| line, or a row that does not name a stage
        title = cells[1]
        if match := TABLE_ENDING.match(title):
            title = match.group(1)
        title = _cut_title(title)
        if title:
            rows.append(StageDraft(int(cells[0]), title))
    return rows


def _refusal(name: str, header: dict[str, str]) -> str | None:
    if name.lower().endswith("-mentor.md"):
        return "Its file name ends in -mentor.md, so it is a mentor's answer key and stays off the site."
    if header.get("draft", "").lower() == "true":
        return "Its header says draft: true, so it stays off the site."
    return None


def _read_note(header: dict[str, str], body: str) -> _Note:
    """Read one file that was not refused."""
    note = _Note(table_rows=_read_tables(body))
    title = header.get("title", "").strip()

    if match := BUILD_TITLE.match(title):
        note.stage = int(match.group(1))
        number = int(match.group(2))
        name = match.group(3).strip()
        note.build = _Build(
            number=number,
            title=_cut_title(name[:1].upper() + name[1:]),
            summary=_cut_summary(header.get("description", "")),
            body=body,
        )
    elif match := WARMUP_TITLE.match(title):
        note.stage = int(match.group(1))
        if body:
            note.warmup = (int(match.group(2)), body)
    elif match := STAGE_TITLE.search(title):
        note.stage = int(match.group(1))
        note.stage_title = _cut_title(match.group(2)) or f"Stage {note.stage}"
        note.chunks, note.build_titles = _read_stage_body(body)
    return note


def _positions(chunks: list[_Chunk], builds: list[int]) -> dict[tuple[str, int], int]:
    """Order one stage: the chunks that feed each build come just before it, in rising order.

    Chunks that feed nothing, or feed a build that is not there, go after the last build.
    """
    chunks = sorted(chunks, key=lambda chunk: chunk.number)
    order: list[tuple[str, int]] = []
    for build in sorted(builds):
        order += [("chunk", chunk.number) for chunk in chunks if chunk.feeds == build]
        order.append(("build", build))
    order += [("chunk", chunk.number) for chunk in chunks if chunk.feeds not in builds]
    return {key: i for i, key in enumerate(order, start=1)}


def plan_import(files: Sequence[tuple[str, str]]) -> Plan:
    """files is (file name, text) pairs, in any order."""
    refused: list[Refused] = []
    notes: list[tuple[str, _Note]] = []
    owners: dict[str, str] = {}  # page slug -> the file that gives it

    # Sorted by name, so when two files clash the one that sorts first wins every time. A file
    # that clashes is refused whole, so a refused file never gives half of its pages.
    for name, text in sorted(files, key=lambda pair: pair[0]):
        header, body = _split_header(text)
        if reason := _refusal(name, header):
            refused.append(Refused(name, reason))
            continue
        note = _read_note(header, _drop_hidden(body))
        slugs = note.slugs()
        if clash := next((s for s in slugs if s in owners), None):
            reason = f"It repeats the page {clash}, which {owners[clash]} already gives."
            refused.append(Refused(name, reason))
            continue
        if clash := next((s for i, s in enumerate(slugs) if s in slugs[:i]), None):
            refused.append(Refused(name, f"It has two sections for the page {clash}."))
            continue
        owners.update(dict.fromkeys(slugs, name))
        notes.append((name, note))

    chunks: dict[tuple[int, int], _Chunk] = {}
    builds: dict[tuple[int, int], _Build] = {}
    for _, note in notes:
        for chunk in note.chunks:
            chunks[(note.stage, chunk.number)] = chunk
        if note.build is not None:
            builds[(note.stage, note.build.number)] = note.build
    # Builds known only by a title in a stage note, when no build note gives the full page.
    for _, note in notes:
        for number, title in note.build_titles.items():
            builds.setdefault((note.stage, number), _Build(number, title, "", ""))

    # Each warm-up goes at the end of its chunk. A second one for the same chunk is refused.
    warmed: dict[tuple[int, int], str] = {}  # (stage, chunk) -> the warm-up file
    unused: list[str] = []
    kept: list[tuple[str, _Note]] = []
    for name, note in notes:
        if note.warmup is not None:
            key = (note.stage, note.warmup[0])
            if key not in chunks:
                unused.append(name)
                continue
            if key in warmed:
                reason = f"It repeats the warm-up for chunk {key[1]} of stage {key[0]} from {warmed[key]}."
                refused.append(Refused(name, reason))
                continue
            warmed[key] = name
            body = f"{chunks[key].body}{WARM_UP_HEADING}{note.warmup[1]}"
            chunks[key] = replace(chunks[key], body=body)
        elif not (note.stage_title or note.build or note.table_rows):
            unused.append(name)
            continue
        kept.append((name, note))

    # Stage titles: a stage note's own header first, then tables in file name order.
    stage_titles: dict[int, str] = {}
    for _, note in kept:
        if note.stage_title is not None:
            stage_titles.setdefault(note.stage, note.stage_title)
    for _, note in kept:
        for row in note.table_rows:
            stage_titles.setdefault(row.number, row.title)

    pages: list[PageDraft] = []
    for stage in sorted({s for s, _ in chunks} | {s for s, _ in builds}):
        stage_titles.setdefault(stage, f"Stage {stage}")
        stage_chunks = [chunk for (s, _), chunk in chunks.items() if s == stage]
        stage_builds = [number for s, number in builds if s == stage]
        position = _positions(stage_chunks, stage_builds)
        for chunk in stage_chunks:
            pages.append(
                PageDraft(
                    stage=stage,
                    kind="chunk",
                    number=chunk.number,
                    slug=_slug(stage, "chunk", chunk.number),
                    title=chunk.title,
                    summary=chunk.summary,
                    minutes=chunk.minutes,
                    position=position[("chunk", chunk.number)],
                    body=chunk.body,
                )
            )
        for number in stage_builds:
            build = builds[(stage, number)]
            pages.append(
                PageDraft(
                    stage=stage,
                    kind="build",
                    number=number,
                    slug=_slug(stage, "build", number),
                    title=build.title,
                    summary=build.summary,
                    minutes=None,
                    position=position[("build", number)],
                    body=build.body,
                )
            )

    return Plan(
        stages=tuple(StageDraft(number, title) for number, title in sorted(stage_titles.items())),
        pages=tuple(sorted(pages, key=lambda page: (page.stage, page.position))),
        refused=tuple(refused),
        unused=tuple(unused),
    )

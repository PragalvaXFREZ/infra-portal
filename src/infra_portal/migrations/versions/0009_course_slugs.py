"""an address for each stage, so it can be a course of its own under /learn

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-12
"""

import re
import unicodedata
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MAX_SLUG = 60


def _slug(title: str) -> str:
    # The same rule as course_admin.new_slug, copied so a later change there cannot change this
    # migration. A long title is cut at the end of a word.
    plain = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-") or "course"
    if len(slug) > MAX_SLUG:
        slug = slug[: MAX_SLUG + 1].rpartition("-")[0] or slug[:MAX_SLUG]
    return slug


def upgrade() -> None:
    # Additive only: one new column, which the code before this revision ignores. It stays
    # nullable, and a stage without a slug is reached as /learn/stage-<number>.
    op.add_column("course_stages", sa.Column("slug", sa.String(length=80), nullable=True))

    # Every stage that exists gets a slug from its title. A step page's old address was
    # /learn/<page slug>, so a stage must not take one of those.
    bind = op.get_bind()
    taken = set(bind.execute(sa.text("SELECT slug FROM lab_pages")).scalars())
    for stage_id, title in bind.execute(sa.text("SELECT id, title FROM course_stages ORDER BY number")).all():
        base = _slug(title)
        slug, n = base, 2
        while slug in taken or re.fullmatch(r"stage-\d+", slug):
            slug, n = f"{base}-{n}", n + 1
        taken.add(slug)
        bind.execute(
            sa.text("UPDATE course_stages SET slug = :slug WHERE id = :id"), {"slug": slug, "id": stage_id}
        )

    op.create_index("ix_course_stages_slug", "course_stages", ["slug"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_course_stages_slug", table_name="course_stages")
    op.drop_column("course_stages", "slug")

"""stages of the learning path, and the place of a page on it

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Additive only: a new table and four new columns. Existing pages get no stage, so they
    # stay ordinary lab pages, and the code before this revision ignores the new columns.
    op.create_table(
        "course_stages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("summary", sa.String(length=300), nullable=False),
        sa.Column("visibility", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("number"),
    )
    op.add_column("lab_pages", sa.Column("stage_id", sa.Integer(), nullable=True))
    op.add_column("lab_pages", sa.Column("position", sa.Integer(), server_default="0", nullable=False))
    op.add_column("lab_pages", sa.Column("number", sa.Integer(), nullable=True))
    op.add_column("lab_pages", sa.Column("minutes", sa.Integer(), nullable=True))
    # SET NULL, so removing a stage keeps its pages.
    op.create_foreign_key(
        "lab_pages_stage_id_fkey", "lab_pages", "course_stages", ["stage_id"], ["id"], ondelete="SET NULL"
    )
    op.create_index("ix_lab_pages_stage_id", "lab_pages", ["stage_id"])


def downgrade() -> None:
    op.drop_index("ix_lab_pages_stage_id", table_name="lab_pages")
    op.drop_constraint("lab_pages_stage_id_fkey", "lab_pages", type_="foreignkey")
    op.drop_column("lab_pages", "minutes")
    op.drop_column("lab_pages", "number")
    op.drop_column("lab_pages", "position")
    op.drop_column("lab_pages", "stage_id")
    op.drop_table("course_stages")

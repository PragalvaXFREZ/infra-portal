"""labs a mentor sent back, and courses a mentor opened for one member

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Additive only: two new tables.
    op.create_table(
        "lab_returns",
        sa.Column("member_id", sa.Integer(), nullable=False),
        sa.Column("page_id", sa.Integer(), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=False),
        sa.Column("link", sa.String(length=500), nullable=True),
        sa.Column("handed_in_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("returned_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("returned_by_id", sa.Integer(), nullable=True),
        # CASCADE: a note means nothing without its member or its page.
        sa.ForeignKeyConstraint(["member_id"], ["members.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["page_id"], ["lab_pages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["returned_by_id"], ["members.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("member_id", "page_id"),
    )
    op.create_index("ix_lab_returns_page_id", "lab_returns", ["page_id"])
    op.create_table(
        "course_grants",
        sa.Column("member_id", sa.Integer(), nullable=False),
        sa.Column("stage_id", sa.Integer(), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("granted_by_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["member_id"], ["members.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["stage_id"], ["course_stages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["granted_by_id"], ["members.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("member_id", "stage_id"),
    )
    op.create_index("ix_course_grants_stage_id", "course_grants", ["stage_id"])


def downgrade() -> None:
    op.drop_index("ix_course_grants_stage_id", table_name="course_grants")
    op.drop_table("course_grants")
    op.drop_index("ix_lab_returns_page_id", table_name="lab_returns")
    op.drop_table("lab_returns")

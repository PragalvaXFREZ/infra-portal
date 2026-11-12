"""a member's ticks on the steps of the learning path

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Additive only: a new table.
    op.create_table(
        "page_progress",
        sa.Column("member_id", sa.Integer(), nullable=False),
        sa.Column("page_id", sa.Integer(), nullable=False),
        sa.Column("done_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        # CASCADE: a tick means nothing without its member or its page.
        sa.ForeignKeyConstraint(["member_id"], ["members.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["page_id"], ["lab_pages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("member_id", "page_id"),
    )
    op.create_index("ix_page_progress_page_id", "page_progress", ["page_id"])


def downgrade() -> None:
    op.drop_index("ix_page_progress_page_id", table_name="page_progress")
    op.drop_table("page_progress")

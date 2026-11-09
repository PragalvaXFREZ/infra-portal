"""email follows

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Additive only: a new table and a nullable column.
    op.create_table(
        "follows",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("author_id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("confirm_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["author_id"], ["members.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("author_id", "email"),
        sa.UniqueConstraint("token"),
    )
    op.create_index("ix_follows_author_id", "follows", ["author_id"])
    op.add_column("posts", sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True))
    # Posts that were published before this change count as announced, so publishing one again
    # after an unpublish does not email anyone about an old post.
    op.execute("UPDATE posts SET notified_at = published_at WHERE published_at IS NOT NULL")


def downgrade() -> None:
    op.drop_column("posts", "notified_at")
    op.drop_index("ix_follows_author_id", table_name="follows")
    op.drop_table("follows")

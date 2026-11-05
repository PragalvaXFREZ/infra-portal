"""profiles and posts

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Additive only: new nullable columns and a new table.
    op.add_column("members", sa.Column("bio", sa.Text(), nullable=True))
    op.add_column("members", sa.Column("website", sa.String(length=200), nullable=True))
    op.create_table(
        "posts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("author_id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["author_id"], ["members.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("author_id", "slug"),
    )
    op.create_index("ix_posts_author_id", "posts", ["author_id"])
    op.create_index("ix_posts_published_at", "posts", ["published_at"])


def downgrade() -> None:
    op.drop_index("ix_posts_published_at", table_name="posts")
    op.drop_index("ix_posts_author_id", table_name="posts")
    op.drop_table("posts")
    op.drop_column("members", "website")
    op.drop_column("members", "bio")

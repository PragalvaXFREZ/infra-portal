"""email subscriptions to every member's posts

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Additive only: two new tables. The previous release never reads them.
    op.create_table(
        "subscribers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("confirm_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
        sa.UniqueConstraint("token"),
    )
    op.create_table(
        "subscriber_mutes",
        sa.Column("subscriber_id", sa.Integer(), nullable=False),
        sa.Column("author_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["subscriber_id"], ["subscribers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["author_id"], ["members.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("subscriber_id", "author_id"),
    )
    # The primary key covers lookups by subscriber. This one keeps the cascade from a deleted member
    # and the "who muted this author" query fast.
    op.create_index("ix_subscriber_mutes_author_id", "subscriber_mutes", ["author_id"])


def downgrade() -> None:
    op.drop_index("ix_subscriber_mutes_author_id", table_name="subscriber_mutes")
    op.drop_table("subscriber_mutes")
    op.drop_table("subscribers")

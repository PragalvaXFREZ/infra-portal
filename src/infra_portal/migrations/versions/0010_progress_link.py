"""a link to the member's work on a lab they hand in

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Additive only: one nullable column, which the code before this revision ignores. Ticks
    # that already exist keep counting as done, with no link.
    op.add_column("page_progress", sa.Column("link", sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column("page_progress", "link")

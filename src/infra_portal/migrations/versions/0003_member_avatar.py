"""uploaded member pictures

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Additive only: two nullable columns.
    op.add_column("members", sa.Column("avatar_image", sa.LargeBinary(), nullable=True))
    op.add_column("members", sa.Column("avatar_version", sa.String(length=16), nullable=True))


def downgrade() -> None:
    op.drop_column("members", "avatar_version")
    op.drop_column("members", "avatar_image")

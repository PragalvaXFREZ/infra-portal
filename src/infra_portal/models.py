"""Database tables.

A change here needs a migration: `uv run alembic revision --autogenerate -m "..."`.
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


ROLE_MEMBER = "member"
ROLE_ADMIN = "admin"


class Member(Base):
    """A person who may sign in.

    An admin creates the row with only `github_login` set: that is the invite.
    The first sign-in fills `github_id`. Later sign-ins match on `github_id`,
    because a GitHub login can be renamed but the numeric ID never changes.
    """

    __tablename__ = "members"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Stored in lowercase. GitHub logins are case-insensitive, 39 characters at most.
    github_login: Mapped[str] = mapped_column(String(39), unique=True)
    github_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    display_name: Mapped[str | None] = mapped_column(String(100))
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    role: Mapped[str] = mapped_column(String(16), default=ROLE_MEMBER)
    invited_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    @property
    def has_signed_in(self) -> bool:
        return self.github_id is not None

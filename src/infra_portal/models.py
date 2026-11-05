"""Database tables.

A change here needs a migration: `uv run alembic revision --autogenerate -m "..."`.
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


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
    bio: Mapped[str | None] = mapped_column(Text)
    website: Mapped[str | None] = mapped_column(String(200))

    posts: Mapped[list["Post"]] = relationship(
        back_populates="author", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def name(self) -> str:
        return self.display_name or self.github_login

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    @property
    def has_signed_in(self) -> bool:
        return self.github_id is not None


STATUS_DRAFT = "draft"
STATUS_PUBLISHED = "published"


class Post(Base):
    """A Markdown post. Only the author sees a draft.

    The slug is set when the post is created and does not follow later title
    edits, so a published link keeps working.
    """

    __tablename__ = "posts"
    __table_args__ = (UniqueConstraint("author_id", "slug"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    author_id: Mapped[int] = mapped_column(
        ForeignKey("members.id", ondelete="CASCADE"), index=True
    )
    slug: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default=STATUS_DRAFT)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    author: Mapped[Member] = relationship(back_populates="posts")

    @property
    def is_published(self) -> bool:
        return self.status == STATUS_PUBLISHED

    @property
    def url(self) -> str:
        return f"/u/{self.author.github_login}/{self.slug}"

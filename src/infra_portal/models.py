"""Database tables.

A change here needs a migration: `uv run alembic revision --autogenerate -m "..."`.
"""

import datetime as dt
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, deferred, mapped_column, relationship


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
    # A picture the member uploaded, re-encoded by the app as a small WebP. When set it wins over
    # the GitHub picture in `avatar_url`, which sign-in refreshes every time. Deferred, so lists of
    # members do not load the image bytes.
    avatar_image: Mapped[bytes | None] = deferred(mapped_column(LargeBinary))
    # A hash of the image. It goes in the picture URL, so a new upload gets a new URL.
    avatar_version: Mapped[str | None] = mapped_column(String(16))

    posts: Mapped[list["Post"]] = relationship(
        back_populates="author", cascade="all, delete-orphan", passive_deletes=True
    )
    follows: Mapped[list["Follow"]] = relationship(
        back_populates="author", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def name(self) -> str:
        return self.display_name or self.github_login

    @property
    def avatar_src(self) -> str | None:
        """The picture to show: the uploaded one if there is one, else the GitHub one."""
        if self.avatar_version:
            return f"/avatars/{self.id}?v={self.avatar_version}"
        return self.avatar_url

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
    # When followers were emailed about this post. Set on the first publish only, so
    # unpublishing and publishing again does not email them twice.
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    author: Mapped[Member] = relationship(back_populates="posts")

    @property
    def is_published(self) -> bool:
        return self.status == STATUS_PUBLISHED

    @property
    def url(self) -> str:
        return f"/u/{self.author.github_login}/{self.slug}"


class Follow(Base):
    """An email address that gets a message when one member publishes a post.

    Anyone can type any address, so a row starts unconfirmed and gets no post emails until the
    link in the confirmation email is opened. `token` is in that link and in every unsubscribe
    link; it is the only thing that proves the reader owns the address.
    """

    __tablename__ = "follows"
    __table_args__ = (UniqueConstraint("author_id", "email"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    author_id: Mapped[int] = mapped_column(
        ForeignKey("members.id", ondelete="CASCADE"), index=True
    )
    # Stored in lowercase.
    email: Mapped[str] = mapped_column(String(254))
    token: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # When the last confirmation email went out. A repeat request within a day sends nothing.
    confirm_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    author: Mapped[Member] = relationship(back_populates="follows")

    @property
    def is_confirmed(self) -> bool:
        return self.confirmed_at is not None


class Subscriber(Base):
    """An email address that gets a message when any member publishes a post.

    It is a rule, not one `follows` row per member, so members who join later are covered. It is
    confirmed the same way as a follow, and `token` plays the same part.
    """

    __tablename__ = "subscribers"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Stored in lowercase.
    email: Mapped[str] = mapped_column(String(254), unique=True)
    token: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # When the last confirmation email went out. A repeat request within a day sends nothing.
    confirm_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Not passive: the ORM deletes the mutes itself. SQLite, used in tests, does not run ON DELETE
    # CASCADE, and it reuses row ids, so leftover mutes could attach to a later subscriber.
    mutes: Mapped[list["SubscriberMute"]] = relationship(
        back_populates="subscriber", cascade="all, delete-orphan"
    )

    @property
    def is_confirmed(self) -> bool:
        return self.confirmed_at is not None


class SubscriberMute(Base):
    """A subscriber who wants every member's posts except this member's."""

    __tablename__ = "subscriber_mutes"

    subscriber_id: Mapped[int] = mapped_column(
        ForeignKey("subscribers.id", ondelete="CASCADE"), primary_key=True
    )
    # Removing a member removes the mutes that point at them, through the database.
    author_id: Mapped[int] = mapped_column(
        ForeignKey("members.id", ondelete="CASCADE"), primary_key=True, index=True
    )

    subscriber: Mapped[Subscriber] = relationship(back_populates="mutes")
    author: Mapped[Member] = relationship()


VISIBILITY_DRAFT = "draft"
VISIBILITY_MEMBERS = "members"
VISIBILITY_PUBLIC = "public"
VISIBILITIES = (VISIBILITY_DRAFT, VISIBILITY_MEMBERS, VISIBILITY_PUBLIC)


class LabPage(Base):
    """A lab, experiment or learning session written on the admin pages instead of in a file.

    The repository and its image are public, so a page that must stay hidden cannot be a file.
    `visibility` decides who sees it: `draft` (admins), `members` (anyone signed in) or `public`.
    The slug is set when the page is created and does not follow later title edits.
    """

    __tablename__ = "lab_pages"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(16))
    date: Mapped[dt.date] = mapped_column(Date)
    summary: Mapped[str] = mapped_column(String(300))
    link: Mapped[str | None] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text, default="")
    visibility: Mapped[str] = mapped_column(String(16), default=VISIBILITY_DRAFT)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    # SET NULL, not CASCADE: removing a member must not remove the course pages they edited.
    # Member has no relationship back to this table on purpose, so the ORM never deletes pages
    # along with a member either.
    edited_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("members.id", ondelete="SET NULL")
    )

    edited_by: Mapped[Member | None] = relationship()

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


class CourseStage(Base):
    """One stage of the course notes, such as "Stage 1: one Linux machine".

    Members meet a stage as a course of its own on the Learn page, at /learn/<slug>.
    A stage holds pages (LabPage rows with `stage_id` set) in the order of their `position`.
    A stage with no written page yet is not listed on the Learn page for anyone.
    `visibility` works as it does for a page. A viewer sees a page on the path only if they may
    see both the page and its stage.
    """

    __tablename__ = "course_stages"

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[int] = mapped_column(unique=True)
    # The course's address under /learn, such as `bread` in /learn/bread. It is made from the
    # title when the stage is created and does not follow later title edits, so links keep working.
    slug: Mapped[str | None] = mapped_column(String(80), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str] = mapped_column(String(300), default="")
    visibility: Mapped[str] = mapped_column(String(16), default=VISIBILITY_DRAFT)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    pages: Mapped[list["LabPage"]] = relationship(
        back_populates="stage", order_by="LabPage.position"
    )

    @property
    def address(self) -> str:
        """The last part of the course's URL. A stage with no slug is reached by its number."""
        return self.slug or f"stage-{self.number}"

    @property
    def url(self) -> str:
        return f"/learn/{self.address}"


class LabPage(Base):
    """A lab, experiment or learning session written on the admin pages instead of in a file.

    The repository and its image are public, so a page that must stay hidden cannot be a file.
    `visibility` decides who sees it: `draft` (admins), `members` (anyone signed in) or `public`.
    The slug is set when the page is created and does not follow later title edits.

    A page with a `stage_id` is a step of a course: a chunk (videos, questions and a warm-up)
    or a build, which members see as a lab. It is served under /learn/<course> and left out of
    the lab lists.
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

    # SET NULL: removing a stage leaves its pages as ordinary lab pages.
    stage_id: Mapped[int | None] = mapped_column(
        ForeignKey("course_stages.id", ondelete="SET NULL"), index=True
    )
    # The order inside the stage, lowest first.
    position: Mapped[int] = mapped_column(default=0, server_default="0")
    # The step's own number in its stage, as the course gives it: 3 for "Chunk 3". It is kept
    # apart from `position`, because the order on the path is not always the order of numbers.
    number: Mapped[int | None] = mapped_column()
    # Minutes of video in a chunk, shown on the path. Empty for pages without videos.
    minutes: Mapped[int | None] = mapped_column()

    edited_by: Mapped[Member | None] = relationship()
    stage: Mapped[CourseStage | None] = relationship(back_populates="pages")

    @property
    def url(self) -> str:
        """Where the page is served: inside its course, or among the labs."""
        if self.stage is not None:
            return f"{self.stage.url}/{self.slug}"
        return f"/labs/{self.slug}"


class PageProgress(Base):
    """A member's own tick on a step of a course: "I have done this one".

    The member sets it and can take it back. It shows where they are, and it shows the mentor
    where everyone is. A course opens once every step of the course before it is ticked
    (course.with_locks). One row per member and page.
    """

    __tablename__ = "page_progress"

    # Both CASCADE: a tick means nothing without its member or its page.
    member_id: Mapped[int] = mapped_column(
        ForeignKey("members.id", ondelete="CASCADE"), primary_key=True
    )
    page_id: Mapped[int] = mapped_column(
        ForeignKey("lab_pages.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    done_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Where the member's work on a lab is: a pull request or their write-up. A lab is handed in
    # with it. Empty on a lesson, and on a lab ticked before links were asked for.
    link: Mapped[str | None] = mapped_column(String(500))


class LabReturn(Base):
    """A lab the mentor sent back to a member, with a short note saying why.

    Sending a lab back takes the member's tick away, so the lab is theirs to hand in again, and
    this row keeps the note for them to read on the lab. The link they handed in is kept here
    too, so the form starts from it. The row goes when they hand the lab in again.
    One row per member and page.
    """

    __tablename__ = "lab_returns"

    # Both CASCADE: a note means nothing without its member or its page.
    member_id: Mapped[int] = mapped_column(
        ForeignKey("members.id", ondelete="CASCADE"), primary_key=True
    )
    page_id: Mapped[int] = mapped_column(
        ForeignKey("lab_pages.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    note: Mapped[str] = mapped_column(String(500))
    link: Mapped[str | None] = mapped_column(String(500))
    # When the member had handed it in, so a lab sent back by mistake gets its own day back.
    handed_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    returned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # SET NULL: removing the mentor must not remove the note.
    returned_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("members.id", ondelete="SET NULL")
    )


class CourseGrant(Base):
    """A course the mentor opened for one member, without the course before it being finished.

    For someone who already knows what an earlier course teaches. The mentor can take it back,
    which locks the course again unless the member has ticked something in it by then.
    One row per member and stage.
    """

    __tablename__ = "course_grants"

    member_id: Mapped[int] = mapped_column(
        ForeignKey("members.id", ondelete="CASCADE"), primary_key=True
    )
    stage_id: Mapped[int] = mapped_column(
        ForeignKey("course_stages.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    granted_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("members.id", ondelete="SET NULL")
    )

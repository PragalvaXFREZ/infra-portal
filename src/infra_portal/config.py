"""Runtime settings.

Every setting comes from an environment variable. This is the contract between
the image and whatever runs it: `compose.yaml` on a laptop, a Kubernetes
Deployment on devata. The image is the same everywhere; only these values change.
"""

from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode


class Settings(BaseSettings):
    # Example: postgresql+psycopg://portal:secret@db:5432/portal
    database_url: str

    # Public address of the site, without a trailing slash. Used to build the
    # OAuth callback URL, so it must match the GitHub OAuth app exactly.
    base_url: str = "http://localhost:8000"

    # GitHub OAuth app credentials. Empty values keep the site up but make
    # sign-in fail with a clear message.
    github_client_id: str = ""
    github_client_secret: str = ""

    # Signs the session cookie. Anyone who knows it can forge a session.
    session_secret: str = Field(min_length=32)

    # Send the session cookie over HTTPS only. Disable only for http://localhost.
    cookie_secure: bool = True

    # GitHub logins that may sign in without an invite and are admins.
    # Comma-separated in the environment: ADMIN_GITHUB_LOGINS=alice,bob
    admin_github_logins: Annotated[list[str], NoDecode] = []

    @field_validator("admin_github_logins", mode="before")
    @classmethod
    def _split_logins(cls, value: object) -> object:
        if isinstance(value, str):
            return [login.strip().lower() for login in value.split(",") if login.strip()]
        return value

    @field_validator("base_url")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @property
    def github_callback_url(self) -> str:
        return f"{self.base_url}/auth/github/callback"


@lru_cache
def get_settings() -> Settings:
    return Settings()

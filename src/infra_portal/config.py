"""Runtime settings.

Every setting comes from an environment variable. This is the contract between
the image and whatever runs it: `compose.yaml` on a laptop, a Kubernetes
Deployment on devata. The image is the same everywhere; only these values change.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Example: postgresql+psycopg://portal:secret@db:5432/portal
    database_url: str


@lru_cache
def get_settings() -> Settings:
    return Settings()

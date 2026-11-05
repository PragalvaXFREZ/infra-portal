"""Database connection."""

from functools import lru_cache

from sqlalchemy import Engine, create_engine, text

from infra_portal.config import get_settings


@lru_cache
def get_engine() -> Engine:
    # pool_pre_ping replaces a dead pooled connection instead of failing the request.
    # connect_timeout keeps a readiness check from hanging when the database is down.
    return create_engine(
        get_settings().database_url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 2},
    )


def database_is_reachable() -> bool:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False

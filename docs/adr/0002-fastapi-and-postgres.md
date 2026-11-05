# 0002. Use FastAPI and PostgreSQL

Date: 2026-11-05

## Status

Accepted

## Context

The application needs user accounts, profiles and posts. Later it should support search across posts
and "related posts". The likely way to build that is with text embeddings stored next to the posts.

## Decision

- **Python with FastAPI** for the backend. Typed request and response models come from Pydantic, and
  the machine learning libraries needed for embeddings are in the same language.
- **PostgreSQL** for storage, through SQLAlchemy and the psycopg 3 driver. The pgvector extension can
  store embeddings in the same database later, so no second datastore is needed.
- **uv** for dependency management. `uv.lock` pins every dependency, and the image build uses it with
  `--locked`, so the image contains exactly what the lock file lists.

Go was considered. It is a good fit for small infrastructure tools, but its ecosystem for embeddings and
model serving is weaker than Python's.

## Consequences

- The image includes a Python runtime, so it is larger than a static Go binary (about 210 MB at
  `v0-compose`).
- Schema changes need a migration tool. Alembic arrives with the first table.

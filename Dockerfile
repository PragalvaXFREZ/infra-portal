# Stage 1: build. uv resolves and installs dependencies into /app/.venv.
FROM python:3.13-slim-trixie AS build

COPY --from=ghcr.io/astral-sh/uv:0.13.3 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_NO_CACHE=1

WORKDIR /app

# Dependencies first, project code second. Code changes far more often than
# dependencies, so this order lets Docker reuse the dependency layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY src ./src
RUN uv sync --locked --no-dev --no-editable

# Stage 2: runtime. Only the Python runtime and the finished virtualenv; no uv,
# no build cache, no source tree.
FROM python:3.13-slim-trixie AS runtime

RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --no-create-home app

COPY --from=build --chown=app:app /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

USER 10001
EXPOSE 8000

# --proxy-headers and --forwarded-allow-ips let the app see the real client
# scheme and address when it runs behind a reverse proxy.
CMD ["uvicorn", "infra_portal.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]

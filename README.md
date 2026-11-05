# infra-portal

Member profiles and blogs for the infra group, served at `infra.pragalva.me`.

Each member has a profile and can write posts. The app runs on
[devata](https://github.com/PragalvaXFREZ/lab), a Talos Kubernetes homelab, and it is built in stages so
that each stage can be read and rebuilt on its own.

## How this repository relates to devata

This repository builds the application and publishes one container image. It knows nothing about the
cluster that runs it.

| This repository | [`lab`](https://github.com/PragalvaXFREZ/lab) |
| --- | --- |
| Source, tests, `Dockerfile` | Which image digest runs on devata |
| Database schema and migrations | The Postgres instance and its storage |
| `compose.yaml` for local development | Secrets, routes, network policy |
| CI that publishes the image | Backups, alerts, dashboards |

The two sides meet at a set of environment variables (see [Configuration](#configuration)). The image is
the same everywhere. Only those values change. See [ADR 0001](docs/adr/0001-separate-app-repository.md).

## Run it locally

Requirements: Docker with Compose.

```bash
cp .env.example .env
docker compose up --build --wait
curl localhost:8000/livez
curl localhost:8000/readyz
```

Stop it with `docker compose down`. Add `--volumes` to delete the database as well.

## Continuous integration

`.github/workflows/ci.yaml` runs on every pull request and on every push to `main`:

1. **unit tests**: `uv run pytest`.
2. **compose stack starts healthy**: starts `compose.yaml` and calls both health endpoints.
3. **build image**: builds the image after both checks pass. On `main` it also pushes the image to
   `ghcr.io/pragalvaxfrez/infra-portal` with the tags `sha-<short commit>` and `main`, and writes the
   pinned reference (`tag@sha256:digest`) to the run summary.

Deployments pin the digest, not a tag. A tag can move. A digest always names the same image.

## Configuration

| Variable | Required | Example |
| --- | --- | --- |
| `DATABASE_URL` | yes | `postgresql+psycopg://portal:secret@db:5432/portal` |

`compose.yaml` builds `DATABASE_URL` from the `POSTGRES_*` values in `.env`.

## Health endpoints

| Endpoint | Meaning | Touches the database |
| --- | --- | --- |
| `GET /livez` | The process is up and answers HTTP. | No |
| `GET /readyz` | The process can do useful work. Returns `503` when Postgres is unreachable. | Yes |

They are separate on purpose. If the liveness check depended on the database, a database outage would
make the orchestrator restart every API container, and the restarts would fix nothing. A failing
readiness check only removes the container from traffic until the database returns.

## Development without Docker

```bash
uv sync
uv run pytest
```

## Stages

Each stage ends with a git tag. Check out a tag to see the project at that point.

| Tag | Adds | Status |
| --- | --- | --- |
| `v0-compose` | API and Postgres in Compose, health endpoints, non-root multi-stage image | Done |
| `v1-ci` | CI runs tests, builds the image, and publishes it to GHCR | Done |
| `v2-login` | GitHub sign-in, invite-only members, sessions | Planned |
| `v3-profiles-posts` | Profiles and Markdown posts with drafts | Planned |
| `v4-on-devata` | Runs on devata through Argo CD at `infra.pragalva.me` | Planned |

## Layout

```
src/infra_portal/   application package
tests/              pytest suite
docs/adr/           architecture decision records
Dockerfile          two-stage image build
compose.yaml        local stack: api + db
.github/workflows/  CI
```

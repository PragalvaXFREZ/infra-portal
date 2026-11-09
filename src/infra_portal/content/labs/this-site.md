---
title: This site
kind: experiment
date: 2026-11-05
link: https://github.com/PragalvaXFREZ/infra-portal
summary: Member profiles and writing, built in stages so that each stage can be read and rebuilt on its own.
---
A FastAPI app with Postgres, running on devata, a Talos Kubernetes homelab. Each stage ends with a git
tag, so you can check out a tag and see the project at that point.

| Tag | Adds |
| --- | --- |
| `v0-compose` | API and Postgres in Compose, health endpoints, non-root multi-stage image |
| `v1-ci` | CI runs tests, builds the image, and publishes it to GHCR |
| `v2-login` | GitHub sign-in, invite-only members, sessions, Alembic migrations |
| `v3-profiles-posts` | Profiles, member directory, Markdown posts with drafts, admin moderation |

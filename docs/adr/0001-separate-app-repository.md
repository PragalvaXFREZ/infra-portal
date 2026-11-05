# 0001. Keep the application in its own repository

Date: 2026-11-05

## Status

Accepted

## Context

devata is managed through the `lab` repository. Argo CD reconciles the cluster from `lab`, so any
commit there can change the cluster. `lab` already builds small first-party images, such as the snapshot
publisher, but those tools exist only because devata exists.

This application has its own audience. Other people will read it as a worked example and build their
own version. A reader should be able to understand the application without knowing Talos, Cilium,
Longhorn or Argo CD.

## Decision

The application lives in this repository. It produces one container image, published to GHCR.

`lab` holds only how devata runs that image: the pinned image digest, the database, secrets, routes,
network policy and backups.

A release takes two steps:

1. A merge here builds and publishes a new image.
2. A pull request in `lab` changes the pinned digest. Argo CD applies it after the merge.

## Consequences

- A merge in this repository never changes the cluster by itself. Every change on devata is a reviewed
  commit in `lab`, and a revert in `lab` is the rollback.
- The application must not depend on devata. All runtime settings come from environment variables.
- A release needs two pull requests. Automation (Renovate) can open the second one later.

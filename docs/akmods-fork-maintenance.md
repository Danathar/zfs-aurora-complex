# Akmods Fork Maintenance

If a term is unfamiliar, check the shared glossary first:
[`docs/glossary.md`](./glossary.md)

## Purpose

This page explains how to maintain the pinned upstream akmods fork reference used by this repository.

Current control points:

- checked-in defaults file [`ci/defaults.json`](../ci/defaults.json)
- workflow/manual env overrides when you need one-off validation

## Why The Pin Exists

This repo does not build against the moving `main` branch of the configured fork.

Instead, it records one exact akmods commit in:

- [`ci/defaults.json`](../ci/defaults.json)

That exact commit is the akmods source used by the GitHub Actions workflow runs.

Why:

1. reproducibility
2. safer debugging when upstream changes behavior
3. easier rollback when a new akmods change breaks the shared cache build

## Update Process

1. inspect the current state of `Danathar/akmods`
2. choose the exact commit you want to test
3. update `AKMODS_UPSTREAM_REF` in [`ci/defaults.json`](../ci/defaults.json)
4. run branch or manual validation first if the change is risky
5. merge only after `main` builds and signs successfully

## What Usually Forces An Update

1. new Fedora kernel behavior that the current pin does not handle
2. upstream fixes around cache layout, dependency lists, or image publishing
3. changes required for future Fedora majors

## What To Validate After Changing The Pin

1. `Build Shared ZFS Akmods Cache` still succeeds
2. shared cache image contains the `kmod-zfs` RPM needed for the supported primary kernel
3. final candidate image still installs ZFS userspace and modules correctly
4. promotion and signing still succeed on `main`

## Plain-Language Model

The moving pieces are:

1. the configured fork repository: `Danathar/akmods`
2. the pinned commit SHA, meaning the exact Git commit ID stored in this repo
3. the temporary clone the workflow run creates in `/tmp/akmods`

How they relate:

1. the workflow run still uses the configured fork as the source repository
2. the workflow run clones only the pinned commit, not the whole moving branch
3. that clone in `/tmp/akmods` exists only for the current run
4. when the run ends, that temporary checkout is discarded

Most important consequence:

- pushing a new commit to `Danathar/akmods` does **not** change this repo's builds until you update the pinned SHA here

## Failure Discipline

If a new akmods pin breaks the build, revert the pin first.
Do not start patching unrelated workflow code until you know the akmods change is really required.

## Important Current Assumption

This repository no longer patches the cloned akmods `Justfile` at runtime.

That means:

1. if this repo needs repo-specific publish-name behavior, that logic must exist in the pinned `Danathar/akmods` commit itself
2. the clone step here is intentionally boring on purpose: clone, check out the exact commit, verify the commit SHA, stop
3. if a future akmods change breaks repo-specific publishing, fix the fork and repin it instead of reintroducing a local patch layer

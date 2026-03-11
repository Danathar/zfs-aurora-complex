# Akmods Fork Maintenance

If a term is unfamiliar, check the shared glossary first:
[`docs/glossary.md`](./glossary.md)

## Purpose

This page explains how to maintain the pinned upstream akmods fork reference used by this repository.

Current control points:

- workflow env `AKMODS_UPSTREAM_REPO`
- workflow env `AKMODS_UPSTREAM_REF`

## Why The Pin Exists

The repo does not build against a floating akmods branch tip.

Why:

1. reproducibility
2. safer debugging when upstream changes behavior
3. easier rollback when a new akmods change breaks the shared cache build

## Update Process

1. inspect the current state of `Danathar/akmods`
2. choose the exact commit you want to test
3. update `AKMODS_UPSTREAM_REF` in all workflow files that define defaults
4. run branch or manual validation first if the change is risky
5. merge only after `main` builds and signs successfully

## What Usually Forces An Update

1. new Fedora kernel behavior that the current pin does not handle
2. upstream fixes around cache layout, dependency lists, or image publishing
3. changes required for future Fedora majors

## What To Validate After Changing The Pin

1. `Build Shared ZFS Akmods Cache` still succeeds
2. merged shared cache image contains `kmod-zfs` RPMs for every base-image kernel
3. final candidate image still installs ZFS userspace and modules correctly
4. promotion and signing still succeed on `main`

## Failure Discipline

If a new akmods pin breaks the build, revert the pin first.
Do not start patching unrelated workflow code until you know the akmods change is really required.

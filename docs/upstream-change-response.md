# Upstream Change Response Guide

If a term is unfamiliar, check the shared glossary first:
[`docs/glossary.md`](./glossary.md)

## Purpose

This guide is the step-by-step response guide for when a workflow run fails.

The important rule is: determine which boundary moved.

In this project the likely moving boundaries are:

1. the Kinoite base image
2. the Fedora kernel set inside that base image
3. the upstream akmods fork behavior
4. the current OpenZFS release line
5. GitHub runner/container tooling behavior

## First Triage Pass

When `build.yml` fails, identify which job failed first:

1. `Build Shared ZFS Akmods Cache`
2. `Build Candidate Image`
3. `Promote Candidate To Stable`

That job boundary tells you where to start.

## Failure: Shared Akmods Cache Check Or Rebuild

Symptoms:

- `check-akmods-cache` reports missing or out-of-date RPMs
- `just build` fails in the akmods worktree
- merged shared cache image is missing one of the required kernels

Likely causes:

1. Fedora shipped a new kernel that current OpenZFS does not support yet
2. the pinned akmods fork needs an update
3. upstream akmods changed assumptions around cache layout

What to inspect:

1. the resolved base image digest and kernel list in the saved `build-inputs` file
2. the failing akmods logs around `just build`
3. the pinned `AKMODS_UPSTREAM_REF`
4. the configured `ZFS_MINOR_VERSION`

Repair path:

1. decide whether OpenZFS support exists for the new kernel
2. if support exists, update the pinned akmods fork or other build logic as needed
3. rerun `build.yml` with `rebuild_akmods=true`
4. if support does not exist yet, let stable remain on the last good build

## Failure: Candidate Image Build

Symptoms:

- `buildah-build` fails during `Containerfile` execution
- `rpm-ostree install distrobox` fails
- `install_zfs_from_akmods_cache.py` fails
- `bootc container lint` fails

What to inspect:

1. `Containerfile`
2. [`build_files/build-image.sh`](../build_files/build-image.sh)
3. [`containerfiles/zfs-akmods/install_zfs_from_akmods_cache.py`](../containerfiles/zfs-akmods/install_zfs_from_akmods_cache.py)

Common cases:

1. the base image no longer includes a command the helper expected
2. the shared akmods cache contains out-of-date or malformed RPMs
3. brew OCI payload layout changed upstream
4. `rpm-ostree` behavior changed in the builder environment

Repair path:

1. reproduce the exact input set from the saved `build-inputs` file
2. verify the shared akmods image still contains the expected RPM names
3. inspect which command failed inside the Containerfile logs
4. patch the helper/build script, not the workflow, when the failure is build-root logic

## Failure: Promotion Or Signing

Symptoms:

- candidate build passed but stable did not move
- `skopeo copy` failed
- `cosign sign` or `cosign verify` failed

What to inspect:

1. whether the candidate tag was actually pushed
2. whether `SIGNING_SECRET` is present in repository secrets
3. whether `cosign.pub` in the repo matches the private key stored in the secret
4. whether GitHub Container Registry (GHCR) permissions for the workflow token are correct

Repair path:

1. confirm the candidate tag exists in GitHub Container Registry (GHCR)
2. confirm `SIGNING_SECRET` is the matching private key for committed `cosign.pub`
3. rerun the workflow without changing the already-built candidate digest if the failure was transient

## Branch Or Pull Request Validation Failures

These workflows intentionally do not rebuild the shared akmods cache.

So if `build-branch.yml` or `build-pr.yml` fails in `prepare-validation-build`, the right repair path is usually:

1. fix `main`
2. refresh the shared akmods cache on `main`
3. rerun branch or pull request validation

That is intentional. Branch and pull request validation should not rewrite the shared image tags used by `main`.

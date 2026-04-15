# Upstream Change Response Guide

If a term is unfamiliar, check the shared glossary first:
[`docs/glossary.md`](./glossary.md)

## Purpose

This guide is the step-by-step response guide for when a workflow run fails.

The important rule is: determine which boundary moved.

In this project the likely moving boundaries are:

1. the Aurora base image
2. the Fedora kernel set inside that base image
3. the upstream akmods fork behavior
4. the current OpenZFS release line
5. GitHub runner/container tooling behavior

## When Manual Intervention Is And Is Not Required

Not every red `build.yml` run needs a human to change a pinned value. The daily cron re-runs the full input resolution and cache check from scratch, so a failure caused by a temporary upstream mismatch is expected to clear itself once upstream catches up. The recovery policy is the same one described in the README: stable stays on the last known-good image while this is happening, and rollback is the supported recovery path.

Use this table before editing any pin:

| Symptom | Does it need a human? |
|---|---|
| One red run, next scheduled run goes green without changes | No — transient upstream state; the cron healed it |
| Red runs in a streak, but OpenZFS has not yet released support for the current Fedora kernel | No — wait for upstream. Stable is still on the last good image |
| Red runs in a streak, and the floating akmods tracking ref has already landed support for the current kernel | Usually no edit - rerun or wait for the next cron. Set `AKMODS_UPSTREAM_REF` only for one-off validation or a temporary freeze |
| Red run with a Python traceback or shell error unrelated to `just build` or `kmod-zfs` | Yes — this is a repo bug, not an upstream mismatch. Treat as a normal code fix |
| Promotion job skipped because candidate failed | No action on the promotion side — fix the candidate failure by whichever branch above applies |

The important idea: a red build is informative, not an emergency. The stable image tag does not move while builds are red, so users are not exposed to a broken image. Manual intervention is only correct when waiting will not fix the problem.

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
- shared cache image is missing the supported primary kernel

Likely causes:

1. Fedora shipped a new kernel that current OpenZFS does not support yet
2. the resolved akmods source ref does not contain the needed support yet
3. upstream akmods changed assumptions around cache layout

What to inspect:

1. the resolved base image digest, detected kernel list, and primary kernel in the saved `build-inputs` file
2. the failing akmods logs around `just build`
3. the resolved akmods source ref and whether `AKMODS_UPSTREAM_REF` is pinned or floating is active
4. the configured `ZFS_MINOR_VERSION`

Repair path:

1. decide whether OpenZFS support exists for the new kernel
2. if support exists on the floating tracking ref, rerun or wait for the next cron
3. if support exists only at a specific SHA, temporarily pin `AKMODS_UPSTREAM_REF`
4. rerun `build.yml` with `rebuild_akmods=true`
5. if support does not exist yet, let stable remain on the last good build

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
2. verify the shared akmods image still contains the expected RPM for the supported primary kernel
3. inspect which command failed inside the Containerfile logs
4. patch the helper/build script, not the workflow, when the failure is build-root logic

## Assumptions Behind This Repo

This repo intentionally follows a narrower support contract than some earlier designs:

1. if the primary kernel does not have a matching ZFS module, the build must fail
2. if a deployed image still proves bad, the recovery path is rollback to the previous image
3. older bundled kernels inside the same image are not treated as a required ZFS compatibility target

Consequence:

1. the repo is simpler than a "support every bundled kernel" design
2. but if someone boots an older bundled kernel from the current image directly, ZFS is not guaranteed there
3. the documented answer to that problem is to roll back the image instead of trying to stay on the current image with an older bundled kernel

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

Pull request validation intentionally does not rebuild the shared akmods cache.
Human-owned branch builds can now refresh the shared cache when they move to a new kernel stream.

So if `build-pr.yml` fails in `prepare-validation-build`, or if `build-branch.yml` fails before it can refresh the cache, the right repair path is usually:

1. fix `main`
2. refresh the shared akmods cache on `main`
3. rerun branch or pull request validation

That is intentional for pull requests. Branch builds are allowed to seed the shared branch-target cache when required, but pull requests still do not rewrite shared image tags.

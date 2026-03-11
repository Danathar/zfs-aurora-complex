# zfs-kinoite-containerfile Architecture Overview

If a term is unfamiliar, check the shared glossary first:
[`docs/glossary.md`](./glossary.md)

## Purpose

This project provides a controlled way to run ZFS on Kinoite while using a
native `Containerfile` instead of BlueBuild.

The technical target is still the same:

1. track the moving Kinoite/Fedora kernel stream
2. build matching ZFS akmods for that exact kernel set
3. install those RPMs into the final image
4. publish stable tags only after candidate succeeds

## Real Simplification Goals

This repository intentionally removes three major layers from the older design:

1. no BlueBuild recipe generation
2. no separate candidate image repository
3. no branch/candidate akmods alias repository flow

That means the main complexity now lives in two places only:

1. input pinning and akmods cache control in `ci_tools/`
2. compose-time ZFS install logic in `containerfiles/zfs-akmods/install_zfs_from_akmods_cache.py`

## Outputs

### OS Image Repository

All OS image tags live in the same repository:

- candidate tag: `ghcr.io/danathar/zfs-kinoite-containerfile:candidate-<sha>-<fedora>`
- stable tag: `ghcr.io/danathar/zfs-kinoite-containerfile:latest`
- stable audit tag: `ghcr.io/danathar/zfs-kinoite-containerfile:stable-<run>-<sha>`
- branch tag: `ghcr.io/danathar/zfs-kinoite-containerfile:br-<branch>-<fedora>`

### Shared Akmods Cache Repository

The shared cache remains separate because it is a different artifact class:

- `ghcr.io/danathar/zfs-kinoite-containerfile-akmods:main-<fedora>`
- `ghcr.io/danathar/zfs-kinoite-containerfile-akmods:main-<fedora>-x86_64`

Why keep a separate akmods cache repository:

1. it keeps the final OS image tags readable
2. it preserves the existing akmods reuse model
3. the cache is build-time infrastructure, not the user-facing OS image

## How It Works

### 1. Input Resolution

The main workflow resolves and pins:

1. base image ref, digest, and stable tag
2. build container ref and digest for the akmods job
3. Fedora major version
4. every installed kernel found in `/lib/modules`
5. pinned akmods fork commit
6. ZFS minor version line

Those values are written to a `build-inputs-<run_id>` artifact for replay.

### 2. Shared Akmods Cache Reuse Or Rebuild

The workflow checks whether the shared cache image already contains a matching
`kmod-zfs-<kernel_release>-...rpm` for every kernel shipped in the base image.

If yes:

- reuse the cache

If no:

1. clone the pinned `Danathar/akmods` fork
2. patch its upstream `Justfile` so publish names come from `images.yaml`
3. point its target output to `ghcr.io/<owner>/zfs-kinoite-containerfile-akmods`
4. build per-kernel payloads when more than one kernel is present
5. merge those payloads into one shared Fedora-wide cache image

### 3. Native Final Image Build

The final image is defined by the repository root [`Containerfile`](../Containerfile).

It does four important things:

1. starts from the pinned `BASE_IMAGE`
2. copies the official `ublue-os/brew` OCI payload into the image root
3. runs [`build_files/build-image.sh`](../build_files/build-image.sh)
4. runs `bootc container lint`

`build-image.sh` then:

1. enables brew setup/update services via `systemctl preset`
2. installs `distrobox` via `rpm-ostree install`
3. runs the ZFS install helper against `AKMODS_IMAGE`
4. writes repository-specific signing policy for `ghcr.io/danathar/zfs-kinoite-containerfile`
5. finalizes the image with `ostree container commit`

### 4. Multi-Kernel ZFS Install Logic

The hardest part remains the same as in the older repo:

1. the shared akmods cache can contain multiple `kmod-zfs-<kernel_release>` RPMs
2. those RPM files still share one RPM identity, `kmod-zfs`
3. `rpm-ostree` will not keep multiple same-name RPM identities installed side-by-side

So the helper does this:

1. install ZFS userspace RPMs and one primary `kmod-zfs` through `rpm-ostree`
2. unpack the remaining kernel-module payloads directly into `/`
3. run `depmod -a <kernel>` for every base kernel
4. fail if any base kernel ends up missing `zfs.ko`

That keeps fallback-kernel module coverage while isolating the complexity in a tested Python helper.

### 5. Promotion And Signing

Promotion is a separate job.

It:

1. resolves the candidate tag digest
2. copies that digest to `latest`
3. copies that digest to `stable-<run>-<sha>`
4. signs candidate after publish
5. signs `latest` after promotion

Because candidate and stable tags are in the same repository, the trust model is much simpler than the old repo:

- no second image namespace
- no stable-vs-candidate policy drift
- no host-side repair script to normalize two repository names

## Operational Model

1. `build.yml`: candidate-first build and promotion
2. `build-branch.yml`: read-only validation inputs plus branch-tagged push
3. `build-pr.yml`: read-only validation inputs plus no-push build

## Design Principles

1. keep stable on the last known-good build when candidate fails
2. keep the final image repo single-path and boring
3. keep the shared akmods cache explicit and inspectable
4. pin run inputs so `latest` drift does not change behavior mid-run
5. keep the hard multi-kernel logic in Python, not inline workflow shell

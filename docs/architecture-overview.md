# zfs-kinoite-containerfile Architecture Overview

If a term is unfamiliar, check the shared glossary first:
[`docs/glossary.md`](./glossary.md)

## Purpose

This project provides a controlled way to run ZFS on Kinoite with a native
`Containerfile` build.

The technical target is still the same:

1. track the moving Kinoite/Fedora kernel stream
2. build matching ZFS akmods, meaning the ZFS kernel-module packages built for that exact kernel set
3. install those RPMs (Red Hat Package Manager package files) into the final image
4. publish stable tags only after candidate succeeds

## Real Simplification Goals

This repository intentionally keeps three things out of the image build flow:

1. no generated recipe layer
2. no separate candidate image repository
3. no branch/candidate akmods alias repository flow

That means the main complexity now lives in two places only:

1. input pinning and akmods cache control in `ci_tools/`
2. image-build-time ZFS install logic in `containerfiles/zfs-akmods/install_zfs_from_akmods_cache.py`

One smaller cleanup also matters for readability:

- repo-owned data-shaping logic now lives in tracked Python helpers instead of
  inline workflow shell wherever that tradeoff is reasonable

## Outputs

### OS Image Repository

All operating-system image tags live in the same repository:

- candidate tag: `ghcr.io/danathar/zfs-kinoite-containerfile:candidate-<sha>-<fedora>`
- stable tag: `ghcr.io/danathar/zfs-kinoite-containerfile:latest`
- stable audit tag: `ghcr.io/danathar/zfs-kinoite-containerfile:stable-<run>-<sha>`
- branch tag: `ghcr.io/danathar/zfs-kinoite-containerfile:br-<branch>-<fedora>`

### Shared Akmods Cache Repository

The shared cache remains separate because it is a different kind of build output:

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

Those values are written to a saved workflow output file named `build-inputs-<run_id>` so the same input set can be replayed later.

The `main` workflow now wraps that whole preparation path in one local action:

- [`.github/actions/prepare-main-akmods/action.yml`](../.github/actions/prepare-main-akmods/action.yml)

That action does four things in one place:

1. resolve and record build inputs
2. upload the build-input manifest
3. verify whether the shared akmods cache can be reused
4. rebuild and republish the shared cache only when required

### 2. Shared Akmods Cache Reuse Or Rebuild

The workflow checks whether the shared cache image already contains a matching
`kmod-zfs-<kernel_release>-...rpm` for the supported primary kernel.

The repo's policy is:

1. detect every installed kernel in the base image for visibility
2. choose the newest detected kernel as the supported primary kernel
3. require ZFS support only for that supported primary kernel
4. use image rollback, not older bundled kernels in the same image, as the recovery path

That check now has two layers:

1. first inspect `main-<fedora>-metadata`, a tiny metadata tag that only carries
   labels listing the supported kernel release
2. if that metadata tag is missing or malformed, fall back to unpacking the full
   shared cache image and checking the RPM filenames directly

Even when the shared cache is reusable, the workflows still clone the pinned
`Danathar/akmods` commit once per run.

Why:

1. a bad akmods pin can hide for a while if the workflow keeps reusing an older shared cache
2. cloning the pinned ref is the cheapest way to prove that the configured commit SHA
   still exists in the configured fork
3. this keeps branch, pull request, push, and schedule paths honest with each other

If yes:

- reuse the cache

If no:

1. clone the pinned `Danathar/akmods` fork
2. point its target output to `ghcr.io/<owner>/zfs-kinoite-containerfile-akmods`
3. build the shared cache image for the supported primary kernel
4. publish the matching `main-<fedora>-metadata` metadata tag

Important design change:

- this repo no longer patches the cloned akmods `Justfile` at runtime
- the repo-specific publish-name logic now lives in the pinned `Danathar/akmods`
  fork commit itself
- that keeps the runtime clone step boring: clone, check out the exact commit, verify the commit SHA, stop

Plain-language summary of the pin:

1. the source repository is still the configured fork, `Danathar/akmods`
2. this repo records one exact commit from that fork in `ci/defaults.json`
3. the GitHub Actions workflow run clones that one commit into `/tmp/akmods` for the current run only
4. pushing new commits to that fork does nothing here until the pin is updated

### 3. Native Final Image Build

The final image is defined by the repository root [`Containerfile`](../Containerfile).

It does four important things:

1. starts from the pinned `BASE_IMAGE`
2. copies the official `ublue-os/brew` Open Container Initiative (OCI) payload into the image root
3. runs [`build_files/build-image.sh`](../build_files/build-image.sh)
4. runs `bootc container lint`

`build-image.sh` then:

1. enables brew setup/update services via `systemctl preset`
2. installs `distrobox` via `rpm-ostree install`
3. runs the ZFS install helper against the resolved akmods cache image reference
4. writes repository-specific signing policy for `ghcr.io/danathar/zfs-kinoite-containerfile`
5. finalizes the image with `ostree container commit`

The signing-policy step is now a pure Python helper:

- [`files/scripts/configure_signing_policy.py`](../files/scripts/configure_signing_policy.py)

That removed the earlier shell script that embedded an inline Python block just
to write `policy.json`.

Fedora-version handling is intentionally dynamic here:

1. workflow runs normally pass an exact `AKMODS_IMAGE` build argument
2. local builds can rely on `AKMODS_IMAGE_TEMPLATE` instead
3. the helper fills in `{fedora}` by asking the selected base image which Fedora
   major version it is based on
4. that keeps the root `Containerfile` from hard-coding `43`, `44`, or any
   other future Fedora major version into its local-build fallback

### 4. Primary-Kernel ZFS Install Logic

This repo no longer tries to keep every bundled kernel inside the current image
ZFS-ready.

Instead, the helper does this:

1. inspect every kernel directory under `/lib/modules`
2. choose the newest detected kernel as the supported primary kernel
3. require one matching `kmod-zfs` RPM for that kernel
4. install ZFS userspace RPMs and that one primary `kmod-zfs` through `rpm-ostree`
5. run `depmod -a <kernel>` for the supported primary kernel
6. fail the build if that supported kernel does not end up with `zfs.ko`

Why this is the chosen tradeoff:

1. the intended safety rule is "do not publish a new image unless the kernel it is expected to boot first has working ZFS"
2. if a deployed image still proves bad, the recovery path is rollback to the previous image
3. that makes support for older bundled kernels inside the current image optional rather than required
4. dropping that broader guarantee removes a large amount of build and compose complexity

Consequence:

- if the current image contains an older bundled kernel and someone boots that older kernel directly, ZFS is not guaranteed to work there
- the documented recovery path is to roll back the image instead

### Retired Design Note: The Older Multi-Kernel Fallback System

Earlier versions of this project used a more complex design.

That older design worked like this:

1. inspect every detected kernel in the base image
2. build kernel-module payloads for every detected kernel
3. merge those payloads back into one shared akmods cache image
4. install one `kmod-zfs` package normally through `rpm-ostree`
5. unpack the remaining kernel-module payloads directly into the image root
6. run `depmod` for every detected kernel

Why it existed:

1. some upstream base images exposed more than one installed kernel under `/lib/modules`
2. the older design tried to guarantee that ZFS would still work even if someone booted an older bundled kernel from the current image
3. that was a stronger guarantee than simple image rollback

Why this repo no longer uses that design:

1. the stated operator goal is simpler: do not publish a new image unless the primary kernel has matching ZFS support
2. if a deployed image still proves bad, the documented answer is to roll back to the previous image and stay there
3. once rollback became the chosen recovery model, supporting every bundled kernel inside the current image stopped being necessary
4. most of the remaining pipeline complexity lived in that broader guarantee

What was intentionally given up:

1. booting an older bundled kernel from the current image is no longer treated as a supported ZFS recovery path
2. the supported recovery path is now image rollback to the previous known-good image

### 5. Promotion And Signing

Promotion is a separate job.

It:

1. resolves the candidate tag digest
2. copies that digest to `latest`
3. copies that digest to `stable-<run>-<sha>`
4. signs candidate after publish
5. signs `latest` after promotion

Because candidate and stable tags are in the same repository, the trust model is simpler:

- no second image path under `ghcr.io`
- no stable-vs-candidate policy drift
- no host-side repair script to normalize two repository names

## Operational Model

1. `build.yml`: candidate-first build and promotion
   - the workflow now uses small Python helpers for registry-context export and
     candidate-tag generation instead of inline shell snippets
2. `build-branch.yml`: read-only validation inputs plus branch-tagged push
   - bot-authored branch runs still build locally but intentionally skip push and signing
   - human-authored branch runs push/sign normally
   - the final branch image tag is now composed by a small Python helper
3. `build-pr.yml`: read-only validation inputs plus no-push build

## Design Principles

1. keep stable on the last known-good build when candidate fails
2. keep the final image repo single-path and boring
3. keep the shared akmods cache explicit and inspectable
4. pin run inputs so `latest` drift does not change behavior mid-run
5. keep the supported-kernel logic in Python, not inline workflow shell
6. keep workflow defaults in one checked-in file instead of copying them across YAML files

One unavoidable exception exists:

- GitHub resolves `jobs.<job>.container.image` before any step can run
- because of that, the akmods job in `build.yml` still carries one literal
  fallback build-container ref next to the checked-in defaults file
- every later step reads the checked-in defaults instead of repeating them

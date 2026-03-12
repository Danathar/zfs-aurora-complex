# ZFS On Kinoite Testing Design

If a term is unfamiliar, check the shared glossary first:
[`docs/glossary.md`](./glossary.md)

## Purpose

This repository is a controlled testbed for ZFS support on Kinoite using a native `Containerfile` build.

The objective is to validate that we can safely:

1. track the current Kinoite/Fedora kernel stream
2. build ZFS kernel modules against that exact kernel set
3. install those modules into the final bootc image
4. fail in the GitHub Actions workflow run before a broken image replaces `latest`

## Constraints And Context

1. Kinoite is an ostree/bootc-style image, so ZFS integration must happen during image build.
2. ZFS compatibility can lag new Fedora kernels.
3. Branch testing must not overwrite `latest`.
4. pull request (PR) validation should exercise the real build logic but should not push anything.
5. The shared akmods cache should be rebuilt only by `main`, not by branch or PR validation.

## Artifact Strategy

### Main Artifacts

1. candidate OS image: `ghcr.io/danathar/zfs-kinoite-containerfile:candidate-<sha>-<fedora>`
2. stable OS image: `ghcr.io/danathar/zfs-kinoite-containerfile:latest`
3. stable audit tag: `ghcr.io/danathar/zfs-kinoite-containerfile:stable-<run>-<sha>`
4. shared akmods cache image: `ghcr.io/danathar/zfs-kinoite-containerfile-akmods:main-<fedora>`
5. shared akmods cache metadata tag: `ghcr.io/danathar/zfs-kinoite-containerfile-akmods:main-<fedora>-metadata`

### Branch Artifacts

1. human-authored branch image: `ghcr.io/danathar/zfs-kinoite-containerfile:br-<branch>-<fedora>`
2. bot-authored branch runs stop after local validation and do not push any public tag
3. shared akmods cache stays the same shared source image; branch builds do not publish branch-specific cache tags

## End-To-End Build Flow

### 1. Detect Base Kernel Stream

The main workflow resolves build inputs in one of two modes:

1. default mode: resolve floating refs to immutable digests and immutable stream tags at run time
2. replay mode: read pinned inputs from [`ci/inputs.lock.json`](../ci/inputs.lock.json)

After resolving the base image, the workflow inspects `/lib/modules` inside the pinned base image so it knows every installed kernel, not just one metadata label.

### 2. Validate Existing Shared Akmods Cache

Before rebuilding akmods, the GitHub Actions workflow run checks whether the shared cache image already contains a matching `kmod-zfs-<kernel_release>` RPM for every base-image kernel.

That check now prefers the metadata tag first:

1. inspect `main-<fedora>-metadata` and read its cached kernel-release label
2. only if that metadata tag is missing or malformed, unpack the full shared cache image and inspect the RPM filenames directly

If any kernel is missing, rebuild is forced.

Separate from cache reuse, every workflow path also clones the pinned
`Danathar/akmods` commit once.

That check exists because:

1. an out-of-date shared cache can hide a broken pin for a while
2. branch and pull request validation should still prove that the configured akmods commit SHA is
   fetchable, even when they intentionally do not rebuild the cache

What "pinned" means here:

1. this repo uses the configured fork, not upstream directly
2. it uses one exact commit from that fork, not the moving `main` branch tip
3. the GitHub Actions workflow run clones that exact commit into `/tmp/akmods` for the current run only
4. updating that fork later does not change the build until this repo's pin is updated

### 3. Build Shared Akmods Cache When Required

If the cache is missing, out of date, or a manual rebuild is requested, the workflow run:

1. clones the pinned `Danathar/akmods` commit
2. points its target output to `zfs-kinoite-containerfile-akmods`
3. seeds cache metadata for every detected kernel
4. builds kernel-specific payloads when needed
5. merges those payloads into one shared Fedora-wide cache image
6. publishes the `main-<fedora>-metadata` metadata tag for future fast-path reuse checks

### 4. Build Candidate Or Branch Image

The final image build is standard OCI composition now.

The workflow passes build arguments directly into [`Containerfile`](../Containerfile):

1. `BASE_IMAGE`
2. `BREW_IMAGE`
3. `AKMODS_IMAGE`
4. `IMAGE_REPO`
5. `SIGNING_KEY_FILENAME`

That means there is no generated workspace and no per-run file mutation layer.

### 5. Sign Published Tags

Tags published outside pull request validation are signed after push by resolving the pushed tag to a digest and then signing that digest.

This keeps signature behavior consistent for:

1. candidate tags
2. branch tags
3. stable `latest`

Branch note:

- only human-authored branch runs push/sign branch tags
- automation accounts such as Dependabot still run the build, but they stop before the GitHub Container Registry (GHCR) push/signing step so the registry does not fill with unsigned test images

### 6. Promote Candidate To Stable

Promotion only copies the tested candidate digest to:

1. `latest`
2. `stable-<run>-<sha>`

Then `latest` is signed explicitly.

## Why This Repo Is Easier To Reason About

1. no generated workspace layer
2. no recipe mutation
3. no second image repository for candidate
4. no candidate/stable repo-policy normalization inside the image
5. no host repair script for dual repository trust drift

## What Is Still Intrinsically Hard

1. Fedora kernel timing vs OpenZFS release timing
2. shared akmods cache rebuild rules
3. multi-kernel fallback support in the final image

Those are the real complexity drivers that remain.

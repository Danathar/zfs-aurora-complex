# zfs-aurora-complex

[![build](https://github.com/Danathar/zfs-aurora-complex/actions/workflows/build.yml/badge.svg?branch=main)](https://github.com/Danathar/zfs-aurora-complex/actions/workflows/build.yml)

GitHub Actions workflow: `build.yml`

> [!NOTE]
> This repository was developed with significant AI assistance and serves as a **reference implementation** demonstrating production-grade CI/CD patterns for building bootable container images with ZFS support. It covers candidate-first promotion, input pinning, digest resolution, shared akmods caching, image signing, and comprehensive unit testing.
>
> For a simpler, more direct approach to the same problem, see [`aurora-zfs-simple`](https://github.com/Danathar/aurora-zfs-simple). That repo is the lightweight daily driver; this one exists to show what a fuller safety and automation pipeline looks like.
>
> The goal here is not feature maximalism. The goal is a clear build-and-publish flow: one image repository, one shared akmods cache image, direct build arguments, and standard Open Container Initiative (OCI) tooling.

This repository builds a signed Aurora image with:

- ZFS userspace and kernel modules installed from a self-hosted akmods cache image, meaning a container image that stores prebuilt ZFS kernel-module packages
- `distrobox`
- Homebrew from the official `ghcr.io/ublue-os/brew:latest` OCI image
- a single-repository signing policy for future signed `bootc upgrade` flows

The documentation in this repository tries to stay readable for someone who is learning these topics while reading. Terms are defined when they first appear where practical, and the glossary fills in the rest.

## Why This Repo Exists

The problem has not changed:

1. Fedora-family images move kernels quickly.
2. ZFS is an out-of-tree kernel module.
3. That means a new Fedora kernel can arrive before a matching OpenZFS release is ready.
4. If you do not gate builds carefully, you can publish an Aurora image whose kernel and ZFS modules do not match.

This repository intentionally uses:

1. a standard `Containerfile`
2. direct `buildah`/Open Container Initiative (OCI) build arguments
3. one image repository (`ghcr.io/danathar/zfs-aurora-complex`)
4. one shared akmods cache repository (`ghcr.io/danathar/zfs-aurora-complex-akmods`)

## Licensing Note

ZFS is distributed under the Common Development and Distribution License (CDDL). The Linux kernel is distributed under version 2 of the GNU General Public License (GPLv2). The Software Freedom Law Center, the Free Software Foundation, and the OpenZFS project itself have long-standing disagreements about whether redistributing a binary kernel module built against a Linux kernel satisfies both licenses. This repository produces exactly such a binary: a `kmod-zfs` package compiled against a Fedora kernel, baked into a published container image.

This is not a legal opinion and nothing in this repository is legal advice. Operators running this image, redistributing it, or using it as a basis for a downstream image should read the [OpenZFS FAQ on licensing](https://openzfs.github.io/openzfs-docs/Project%20and%20Community/FAQ.html#licensing) and decide for themselves whether their use falls inside what they are comfortable shipping.

## Safety Model

Stable users should only see tested outputs.

So the `main` GitHub Actions workflow does this:

1. resolve and pin the exact base image, detected kernel list, primary boot kernel, builder image, and ZFS line for the run
2. reuse or rebuild the shared akmods cache image for that primary kernel
3. build a candidate image tag in the same repository
4. sign that candidate digest
5. promote the tested candidate digest to `latest` and to an immutable audit tag
6. sign the promoted `latest` digest

If candidate fails, `latest` does not move.

## Assumptions And Recovery Policy

This repo intentionally follows a simpler support contract:

1. the build must fail if ZFS does not match the primary kernel the image is expected to boot first
2. the build does not promise ZFS support for older kernels that may also be present inside the same image
3. if a deployed image turns out to be bad anyway, the recovery path is image rollback to the previous known-good image

That means this repo optimizes for:

1. not publishing a bad new image
2. keeping rollback to the previous image simple
3. reducing complexity inside the build pipeline

It does not optimize for:

1. booting an older bundled kernel inside the current image and still expecting ZFS to work there

Operator rule:

1. if a newly deployed image fails, roll back to the previous known-good image
2. stay on that previous image until this repo successfully publishes a newer image whose primary kernel has matching ZFS support
3. do not treat "boot an older bundled kernel from the bad current image" as the intended recovery workflow

## What Gets Published

All of these tags are stored in GitHub Container Registry (GHCR), which is the container-image registry behind `ghcr.io`.

OS image tags in one repository:

- candidate image: `ghcr.io/danathar/zfs-aurora-complex:candidate-<sha>-<fedora>`
- stable image: `ghcr.io/danathar/zfs-aurora-complex:latest`
- stable audit tag: `ghcr.io/danathar/zfs-aurora-complex:stable-<run>-<sha>`
- branch test image: `ghcr.io/danathar/zfs-aurora-complex:br-<branch>-<fedora>`
  - bot-authored branch runs validate locally but intentionally do not push this tag

Shared akmods cache image:

- `ghcr.io/danathar/zfs-aurora-complex-akmods:main-<fedora>`
- architecture-specific inspection tag: `ghcr.io/danathar/zfs-aurora-complex-akmods:main-<fedora>-x86_64`

The important simplification is this:

- there is no separate `*-candidate` image repository anymore
- there is no branch-scoped public akmods alias repo anymore
- there is no host repair script for stable-vs-candidate trust drift anymore

## How Akmods Source Is Chosen

This repository uses the configured akmods fork:

- `https://github.com/Danathar/akmods.git`

By default it follows the configured tracking ref, but each workflow run resolves
that ref to one exact commit SHA before building anything.

The selection order is:

1. an explicit `AKMODS_UPSTREAM_REF` or `DEFAULT_AKMODS_REF` environment
   override, if one is set
2. the `AKMODS_UPSTREAM_REF` pin in [`ci/defaults.json`](./ci/defaults.json),
   if that field is non-empty
3. otherwise, `AKMODS_UPSTREAM_TRACK` from [`ci/defaults.json`](./ci/defaults.json),
   currently `main`, resolved with `git ls-remote`

Right now [`ci/defaults.json`](./ci/defaults.json) contains the fork URL, the
tracking ref, an empty pin field, image names, image defaults, and the OpenZFS
minor line. Because `AKMODS_UPSTREAM_REF` is empty, floating tracking is the
active default.

What that means in practice:

1. a GitHub Actions workflow run (usually shortened to CI, for continuous
   integration) resolves `Danathar/akmods@main` to one commit SHA at the start
   of the run
2. it makes a temporary clone of that fork into `/tmp/akmods`
3. it verifies that Git checked out the resolved commit
4. it uses that temporary checkout for the rest of the akmods build

What it does **not** mean:

1. the workflow run is not creating a new long-lived clone anywhere in the GitHub
   account that owns the fork
2. the workflow run is not ignoring the configured fork
3. the workflow run is not following a moving branch while the build is already
   in progress

If the fork is updated after upstream changes:

1. under the floating default, the next run resolves the tracking ref again and
   can pick up the new commit automatically
2. when you need to freeze or debug a specific commit, set `AKMODS_UPSTREAM_REF`
   in [`ci/defaults.json`](./ci/defaults.json)
3. clear `AKMODS_UPSTREAM_REF` back to `""` when the temporary pin is no longer
   needed so floating tracking resumes

## Repository Layout

```text
Containerfile                         native image build definition
build_files/build-image.sh            build-time orchestration inside the image
containerfiles/zfs-akmods/            compose-time ZFS install helper
shared/                               shared Python helpers copied into CI and image build context
ci/defaults.json                      checked-in defaults shared by workflows and helpers
files/scripts/                        image-local helper scripts
ci_tools/                             workflow helper commands
.github/actions/                      local composite actions used by the workflows
.github/workflows/                    GitHub Actions pipelines
.github/scripts/README.md             workflow step -> command-line interface (CLI) command map
docs/                                 teaching-style documentation
```

## Core Workflows

- `.github/workflows/build.yml`
  - main push/schedule/manual workflow
  - candidate-first build and promotion
- `.github/workflows/build-branch.yml`
  - branch-tagged test builds
  - reuse or rebuild the shared akmods cache when the branch targets a new primary kernel
- `.github/workflows/build-pr.yml`
  - pull request (PR) validation build
  - no push and no signing
- `.github/workflows/test.yml`
  - Python unit tests for all CI tool modules
  - runs on pull requests and pushes to main

Docs-only changes do not trigger image builds.

## Native Build Flow

At a high level, the final image build now works like this:

1. `Containerfile` starts from `ghcr.io/ublue-os/aurora`
2. `COPY --from=ghcr.io/ublue-os/brew:latest /system_files /` imports the official brew payload
3. `build_files/build-image.sh` enables the brew services/timers, installs `distrobox`, installs ZFS RPMs (Red Hat Package Manager package files) from the shared akmods cache image, writes signing policy, and commits the ostree container
4. `bootc container lint` validates the final image

Three workflow-side simplifications now support that image build:

1. `ci/defaults.json` is the one checked-in source of truth for default image refs, image names, and akmods source-selection settings
2. cache checks now inspect the shared akmods cache image directly, which removes the extra sidecar image and keeps the reuse rule easier to follow
3. small repo-owned Python helpers now handle registry-context export, candidate-tag generation, branch-tag composition, and signing-policy file generation instead of leaving that logic inline in workflow or shell snippets

One Fedora-version detail matters here:

1. GitHub Actions usually passes an exact `AKMODS_IMAGE` reference for the detected Fedora major version
2. local builds do not need a hard-coded Fedora major version in `Containerfile`
3. when `AKMODS_IMAGE` is not passed, the install helper renders `AKMODS_IMAGE_TEMPLATE`
   with the Fedora major version detected from the chosen base image itself

The ZFS install step follows the repo policy above:

1. inspect every detected kernel under `/lib/modules`
2. choose the newest detected kernel as the supported primary kernel
3. require a matching `kmod-zfs` RPM for that kernel
4. install only that kernel's `kmod-zfs` package through `rpm-ostree`
5. run `depmod` for that supported kernel

If the base image carries older bundled kernels too, those older kernels are not treated as supported ZFS targets inside the same image. The recovery path for a bad image is rollback to the previous image, not booting an older bundled kernel from the current one.

That logic lives in:

- [`containerfiles/zfs-akmods/install_zfs_from_akmods_cache.py`](./containerfiles/zfs-akmods/install_zfs_from_akmods_cache.py)

## Local Build

CI uses [`.github/actions/build-native-image`](./.github/actions/build-native-image/action.yml), which wraps `redhat-actions/buildah-build`. For local iteration you can invoke `podman build` directly against the repository root. `AKMODS_IMAGE` is the only build argument that is genuinely required outside CI, because the shared akmods cache image is the source of the `kmod-zfs` RPM for the primary kernel.

```bash
podman build \
    --build-arg BASE_IMAGE=ghcr.io/ublue-os/aurora:latest \
    --build-arg AKMODS_IMAGE=ghcr.io/danathar/zfs-aurora-complex-akmods:main-43 \
    -t zfs-aurora-complex:local \
    .
```

Notes:

1. the `AKMODS_IMAGE` tag must match the Fedora major version of the chosen base image; inspect the base image (`skopeo inspect docker://<base>`) to confirm which `main-<fedora>` tag to reference
2. `AKMODS_IMAGE` can be omitted for offline experiments; the install helper falls back to `AKMODS_IMAGE_TEMPLATE` and auto-detects the Fedora version from the base image, but that fallback still requires network access to pull the cache image
3. local builds do not go through the candidate-before-promote flow or signing; the resulting image tag is ephemeral and is not trusted by any `bootc` policy

For reproducing a specific published image exactly, prefer the CI workflow with `use_input_lock=true` (see [`ci/inputs.lock.json`](./ci/inputs.lock.json)) rather than a local `podman build`. The lock-file path records the exact base image digest, akmods fork SHA, and kernel set of a prior run.

## Install And Rebase

> [!WARNING]
> This is an experimental image stream.

Fresh stock Aurora:

```bash
sudo bootc switch ghcr.io/danathar/zfs-aurora-complex:latest
systemctl reboot
```

Why this image flow stays easier to reason about:

1. the stable and candidate image tags live in the same repository
2. after you boot into this image family once, the in-image policy only needs to trust one repository path
3. there is no dual-repository policy normalization or host repair path to keep in sync

## Quick Validation After Boot

```bash
rpm -q kmod-zfs
modinfo zfs | head
zpool --version
zfs --version
distrobox --version
brew --version
```

For virtual machine (VM) testing with a secondary disk:

```bash
sudo wipefs -a /dev/vdb
sudo zpool create -f -o ashift=12 -O mountpoint=none testpool /dev/vdb
sudo zfs create -o mountpoint=/var/mnt/testpool testpool/data
sudo zpool status
sudo zfs list
```

## Signature Verification

```bash
cosign verify --key cosign.pub ghcr.io/danathar/zfs-aurora-complex:latest
```

## Reading Order

If you want the full technical design and workflow details, read:

1. [`docs/glossary.md`](./docs/glossary.md)
2. [`docs/documentation-guide.md`](./docs/documentation-guide.md)
3. [`docs/architecture-overview.md`](./docs/architecture-overview.md)
4. [`docs/code-reading-guide.md`](./docs/code-reading-guide.md)
5. [`docs/zfs-aurora-testing.md`](./docs/zfs-aurora-testing.md)
6. [`docs/upstream-change-response.md`](./docs/upstream-change-response.md)
7. [`docs/akmods-fork-maintenance.md`](./docs/akmods-fork-maintenance.md)
8. [`.github/scripts/README.md`](./.github/scripts/README.md)

## References

- `Danathar/aurora-zfs-simple`: https://github.com/Danathar/aurora-zfs-simple (simpler daily-driver approach)
- `ublue-os/brew`: https://github.com/ublue-os/brew
- OpenZFS releases: https://github.com/openzfs/zfs/releases

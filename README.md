# zfs-kinoite-containerfile

[![Build Main Image](https://github.com/Danathar/zfs-kinoite-containerfile/actions/workflows/build.yml/badge.svg)](https://github.com/Danathar/zfs-kinoite-containerfile/actions/workflows/build.yml)

> [!NOTE]
> This repository was developed almost entirely with AI assistance. I was more a conductor than a player on this thing. I think anyone using open-source tools on GitHub should have that context before relying on them.
>
> This was as much about having fun on a Friday afternoon with AI as it was about seeing whether this idea could be done. Treat it as such. Please do not use this in production for **ANYTHING**.
>
> This repo is **THE** definition of *brittle*! Honestly, it's probably FAR easier to do what groups like Universal Blue do and manually track and hold back releases until the correct ZFS release is available than to maintain this insane bit of code the AI has tried to make while covering all possibilities.
>
> It is probably WAY more complicated than it needs to be. I'm still reading through the code it made. That said, it does work, seemingly. ;)
>
> This repository is the native-Containerfile follow-up to the older BlueBuild-based `Kinoite-ZFS` repository. The goal here is not feature maximalism. The goal is a simpler control plane: one image repository, one shared akmods cache image, direct build arguments, and standard OCI tooling.

This repository builds a signed Kinoite image with:

- ZFS userspace and kernel modules installed from a self-hosted akmods cache image
- `distrobox`
- Homebrew from the official `ghcr.io/ublue-os/brew:latest` OCI image
- a single-repository signing policy for future signed `bootc upgrade` flows

## Why This Repo Exists

The problem has not changed:

1. Fedora-family images move kernels quickly.
2. ZFS is an out-of-tree kernel module.
3. That means a new Fedora kernel can arrive before a matching OpenZFS release is ready.
4. If you do not gate builds carefully, you can publish a Kinoite image whose kernel and ZFS modules do not match.

What changed here is the image build model.

The older repo used BlueBuild and a generated build workspace. This repo intentionally does not.
Instead it uses:

1. a standard `Containerfile`
2. direct `buildah`/OCI build arguments
3. one image repository (`ghcr.io/danathar/zfs-kinoite-containerfile`)
4. one shared akmods cache repository (`ghcr.io/danathar/zfs-kinoite-containerfile-akmods`)

## Safety Model

Stable users should only see tested outputs.

So the `main` workflow does this:

1. resolve and pin the exact base image, kernel set, builder image, and ZFS line for the run
2. reuse or rebuild the shared akmods cache image for that exact kernel set
3. build a candidate image tag in the same repository
4. sign that candidate digest
5. promote the tested candidate digest to `latest` and to an immutable audit tag
6. sign the promoted `latest` digest

If candidate fails, `latest` does not move.

## What Gets Published

OS image tags in one repository:

- candidate image: `ghcr.io/danathar/zfs-kinoite-containerfile:candidate-<sha>-<fedora>`
- stable image: `ghcr.io/danathar/zfs-kinoite-containerfile:latest`
- stable audit tag: `ghcr.io/danathar/zfs-kinoite-containerfile:stable-<run>-<sha>`
- branch test image: `ghcr.io/danathar/zfs-kinoite-containerfile:br-<branch>-<fedora>`

Shared akmods cache image:

- `ghcr.io/danathar/zfs-kinoite-containerfile-akmods:main-<fedora>`
- architecture-specific inspection tag: `ghcr.io/danathar/zfs-kinoite-containerfile-akmods:main-<fedora>-x86_64`

The important simplification is this:

- there is no separate `*-candidate` image repository anymore
- there is no branch-scoped public akmods alias repo anymore
- there is no host repair script for stable-vs-candidate trust drift anymore

## Repository Layout

```text
Containerfile                         native image build definition
build_files/build-image.sh            build-time orchestration inside the image
containerfiles/zfs-akmods/            compose-time ZFS install helper
files/scripts/                        image-local helper scripts
ci_tools/                             workflow helper commands
.github/workflows/                    GitHub Actions pipelines
.github/scripts/README.md             workflow step -> CLI command map
docs/                                 teaching-style documentation
```

## Core Workflows

- `.github/workflows/build.yml`
  - main push/schedule/manual workflow
  - candidate-first build and promotion
- `.github/workflows/build-branch.yml`
  - branch-tagged test builds
  - read-only reuse of the shared akmods cache
- `.github/workflows/build-pr.yml`
  - pull-request validation build
  - no push and no signing

Docs-only changes do not trigger image builds.

## Native Build Flow

At a high level, the final image build now works like this:

1. `Containerfile` starts from `ghcr.io/ublue-os/kinoite-main`
2. `COPY --from=ghcr.io/ublue-os/brew:latest /system_files /` imports the official brew payload
3. `build_files/build-image.sh` enables the brew services/timers, installs `distrobox`, installs ZFS RPMs from the shared akmods cache image, writes signing policy, and commits the ostree container
4. `bootc container lint` validates the final image

The ZFS install step still has one important workaround:

- if the base image ships more than one installed kernel under `/lib/modules`, the helper installs one `kmod-zfs` package through `rpm-ostree` and unpacks the remaining kernel payloads directly into the image root before running `depmod`

That logic lives in:

- [`containerfiles/zfs-akmods/install_zfs_from_akmods_cache.py`](./containerfiles/zfs-akmods/install_zfs_from_akmods_cache.py)

## Install And Rebase

> [!WARNING]
> This is an experimental image stream.

Fresh stock Kinoite:

```bash
sudo bootc switch ghcr.io/danathar/zfs-kinoite-containerfile:latest
systemctl reboot
```

Why this is simpler than the old repo:

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

For VM testing with a secondary disk:

```bash
sudo wipefs -a /dev/vdb
sudo zpool create -f -o ashift=12 -O mountpoint=none testpool /dev/vdb
sudo zfs create -o mountpoint=/var/mnt/testpool testpool/data
sudo zpool status
sudo zfs list
```

## Signature Verification

```bash
cosign verify --key cosign.pub ghcr.io/danathar/zfs-kinoite-containerfile:latest
```

## Reading Order

If you want the full technical design and workflow details, read:

1. [`docs/glossary.md`](./docs/glossary.md)
2. [`docs/documentation-guide.md`](./docs/documentation-guide.md)
3. [`docs/architecture-overview.md`](./docs/architecture-overview.md)
4. [`docs/code-reading-guide.md`](./docs/code-reading-guide.md)
5. [`docs/zfs-kinoite-testing.md`](./docs/zfs-kinoite-testing.md)
6. [`docs/upstream-change-response.md`](./docs/upstream-change-response.md)
7. [`docs/akmods-fork-maintenance.md`](./docs/akmods-fork-maintenance.md)
8. [`.github/scripts/README.md`](./.github/scripts/README.md)

## References

- `ublue-os/brew`: https://github.com/ublue-os/brew
- `ublue-os/image-template`: https://github.com/ublue-os/image-template
- OpenZFS releases: https://github.com/openzfs/zfs/releases

# zfs-aurora-complex

[![build](https://github.com/Danathar/zfs-aurora-complex/actions/workflows/build.yml/badge.svg?branch=main)](https://github.com/Danathar/zfs-aurora-complex/actions/workflows/build.yml)

[![last good build](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2FDanathar%2Fzfs-aurora-complex%2Fstatus%2Flast-good-build-badge.json)](https://github.com/Danathar/zfs-aurora-complex/pkgs/container/zfs-aurora-complex)

[![OpenZFS/kernel status](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2FDanathar%2Fzfs-aurora-complex%2Fstatus%2Fakmods-badge.json)](https://github.com/Danathar/zfs-aurora-complex/issues?q=is%3Aissue+is%3Aopen+label%3Aakmods-failure)

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/Danathar/zfs-aurora-complex)

## Why This Repo Exists

The problem has not changed:

1. Fedora-family images move kernels quickly.
2. ZFS is an out-of-tree kernel module.
3. That means a new Fedora kernel can arrive before a matching OpenZFS release is ready.
4. If you do not gate builds carefully, you can publish an Aurora DX image whose kernel and ZFS modules do not match.

This repository intentionally uses:

1. a standard `Containerfile`
2. direct `buildah`/Open Container Initiative (OCI) build arguments
3. one image repository (`ghcr.io/danathar/zfs-aurora-complex`)
4. one shared akmods cache repository (`ghcr.io/danathar/zfs-aurora-complex-akmods`)

This repository builds a signed Aurora DX image with:

- ZFS userspace and kernel modules installed from a self-hosted akmods cache image, meaning a container image that stores prebuilt ZFS kernel-module packages
- Distrobox inherited from the upstream Aurora DX image
- Homebrew inherited from the upstream Aurora DX image
- a single-repository signing policy for future signed `bootc upgrade` flows

OpenZFS itself is not hand-pinned to a patch version baked into this repo. Each build resolves
the latest stable release in a configured minor line (`ZFS_MINOR_VERSION`, `2.4` by default —
see [`ci/defaults.json`](./ci/defaults.json)) directly from
[OpenZFS's own GitHub releases](https://github.com/openzfs/zfs/releases) at build time, and
that is the version it attempts to build and install.

The documentation in this repository tries to stay readable for someone who is learning these topics while reading. Terms are defined when they first appear where practical, and the glossary fills in the rest.

GitHub Actions workflow: `build.yml`

> [!IMPORTANT]
> **First switch command for this image:**
>
> ```bash
> sudo bootc switch --enforce-container-sigpolicy ghcr.io/danathar/zfs-aurora-complex:latest
> sudo systemctl reboot
> ```
>
> Do not use plain `bootc switch` for the first move from stock Aurora DX into this
> image. This image installs a repository-specific signing policy for
> `ghcr.io/danathar/zfs-aurora-complex`, so the first switch should record the
> deployment as policy-verified. After rebooting into this image family, future
> updates should be normal:
>
> ```bash
> sudo bootc upgrade
> ```

> [!IMPORTANT]
> **This is now a production system, not a demonstration.** The author runs this
> image as a daily driver on real hardware with real ZFS pools — this repo eats
> its own dog food. Changes here can break a booted machine and can put pooled
> data at risk, so they are held to a production standard: understand the
> blast radius before changing the build, promotion, or signing path.
>
> If you are an AI agent working in this repository, read
> [`CLAUDE.md`](./CLAUDE.md) before making changes. It describes the
> production-safety rules that apply here.

> [!NOTE]
> This repository was developed with significant AI assistance. It demonstrates
> production-grade CI/CD patterns for building bootable container images with ZFS
> support: candidate-first promotion, input pinning, digest resolution, shared
> akmods caching, image signing, and comprehensive unit testing.
>
> For a simpler, more direct approach to the same problem, see [`aurora-zfs-simple`](https://github.com/Danathar/aurora-zfs-simple). That repo is the minimal expression of the same idea; this one carries the fuller safety and automation pipeline.
>
> The goal here is not feature maximalism. The goal is a clear build-and-publish flow: one image repository, one shared akmods cache image, direct build arguments, and standard Open Container Initiative (OCI) tooling.

## Install

> [!WARNING]
> This is a single-maintainer image stream. It is production for its author --
> daily-driven on real hardware with real ZFS pools -- but the bar it has cleared
> is "safe enough for one person's own machines", not a vendor support
> commitment. The pipeline builds, signs, and promotes automatically, but nothing
> in it boots the image or imports a pool before `:latest` moves. Switching a
> machine you depend on onto this image means trusting that bar, not a guarantee.

```bash
sudo bootc switch --enforce-container-sigpolicy ghcr.io/danathar/zfs-aurora-complex:latest
sudo systemctl reboot
```

`--enforce-container-sigpolicy` is required on the first switch, not optional --
it records the deployment as policy-verified instead of as an unverified
registry image. Afterwards, `sudo bootc upgrade` is the normal path.

Full steps, post-boot validation commands, and manual signature verification:
[`docs/install-and-verify.md`](./docs/install-and-verify.md).

## Documentation

Start here depending on what you want:

| I want to... | Read |
|---|---|
| run this image on a machine | [`docs/install-and-verify.md`](./docs/install-and-verify.md) |
| know what this promises, and what to do when a build is bad | [`docs/safety-model.md`](./docs/safety-model.md) |
| build or fork it myself | [`docs/building-locally.md`](./docs/building-locally.md) |
| understand the design | [`docs/architecture-overview.md`](./docs/architecture-overview.md) |
| find my way around the code | [`docs/code-reading-guide.md`](./docs/code-reading-guide.md) |
| understand image signing and bootc trust | [`docs/signing-and-bootc.md`](./docs/signing-and-bootc.md) |
| fix a broken build | [`docs/upstream-change-response.md`](./docs/upstream-change-response.md) |
| read the deep design history and validation notes | [`docs/zfs-aurora-testing.md`](./docs/zfs-aurora-testing.md) |
| change which akmods commit is built | [`docs/akmods-fork-maintenance.md`](./docs/akmods-fork-maintenance.md) |
| look up a term | [`docs/glossary.md`](./docs/glossary.md) |
| see the whole documentation map | [`docs/documentation-guide.md`](./docs/documentation-guide.md) |

The CDDL/GPLv2 position on redistributing a binary ZFS module is recorded in
[`docs/licensing.md`](./docs/licensing.md). It is not legal advice; read it
before redistributing this image or basing a downstream image on it.

## References

- `Danathar/aurora-zfs-simple`: https://github.com/Danathar/aurora-zfs-simple (simpler daily-driver approach)
- `ublue-os/brew`: https://github.com/ublue-os/brew
- OpenZFS releases: https://github.com/openzfs/zfs/releases

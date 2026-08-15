# zfs-aurora-complex

[![build](https://github.com/Danathar/zfs-aurora-complex/actions/workflows/build.yml/badge.svg?branch=main)](https://github.com/Danathar/zfs-aurora-complex/actions/workflows/build.yml)

[![last good build](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2FDanathar%2Fzfs-aurora-complex%2Fstatus%2Flast-good-build-badge.json)](https://github.com/Danathar/zfs-aurora-complex/pkgs/container/zfs-aurora-complex)

[![OpenZFS/kernel status](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2FDanathar%2Fzfs-aurora-complex%2Fstatus%2Fakmods-badge.json)](https://github.com/Danathar/zfs-aurora-complex/issues?q=is%3Aissue+is%3Aopen+label%3Aakmods-failure)

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/Danathar/zfs-aurora-complex)

## Why This Repo Exists

Fedora-family images move kernels quickly, and ZFS is an out-of-tree kernel module — so a new
Fedora kernel can land before a matching OpenZFS release exists. Build that carelessly and you
publish an Aurora DX image whose kernel and ZFS modules do not match.

This repository builds a signed Aurora DX image with ZFS userspace and kernel modules installed
from a self-hosted akmods cache image (a container image holding prebuilt ZFS kernel-module
packages), Distrobox and Homebrew inherited from upstream Aurora DX, and a single-repository
signing policy for signed `bootc upgrade`. It deliberately stays close to standard tooling: one
`Containerfile`, direct `buildah`/Open Container Initiative (OCI) build arguments, one image
repository (`ghcr.io/danathar/zfs-aurora-complex`), and one shared akmods cache repository
(`ghcr.io/danathar/zfs-aurora-complex-akmods`).

OpenZFS is not hand-pinned to a patch version. Each build resolves the newest stable release in
a configured minor line (`ZFS_MINOR_VERSION`, `2.4` by default — see
[`ci/defaults.json`](./ci/defaults.json)) from
[OpenZFS's own GitHub releases](https://github.com/openzfs/zfs/releases) at build time, and that
is the version it attempts to build and install.

> [!IMPORTANT]
> **Changing this repository is a production change, not a demonstration.** A bad build can
> break a booted machine and put pooled data at risk, so the build, promotion, and signing paths
> are held to a production standard — understand the blast radius before changing them. AI agents
> working here must read [`CLAUDE.md`](./CLAUDE.md) first.

> [!NOTE]
> Developed with significant AI assistance. For a simpler, more direct approach to the same
> problem, see [`aurora-zfs-simple`](https://github.com/Danathar/aurora-zfs-simple) — the minimal
> expression of the same idea. This repo is the one the author daily-drives, and carries the
> fuller pipeline: candidate-first promotion, input pinning, digest resolution, shared akmods
> caching, image signing, and unit tests throughout.

## Install

> [!WARNING]
> This is a single-maintainer image stream. It is production for its author --
> daily-driven on real hardware with real ZFS pools -- but the bar it has cleared
> is "safe enough for one person's own machines", not a vendor support
> commitment. **CI does not boot the image or import a pool before `:latest`
> moves**; promotion proves the image composed, is correctly signed, and passed
> `bootc container lint`, nothing more. The recovery model is image rollback, not
> pre-publication runtime testing -- see
> [`docs/safety-model.md`](./docs/safety-model.md). Switching a machine you
> depend on onto this image means trusting that bar, not a guarantee.

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
| read the deep design history and validation notes | [`docs/design-deep-dive.md`](./docs/design-deep-dive.md) |
| change which akmods commit is built | [`docs/akmods-fork-maintenance.md`](./docs/akmods-fork-maintenance.md) |
| look up a term | [`docs/glossary.md`](./docs/glossary.md) |
| see the whole documentation map | [`docs/documentation-guide.md`](./docs/documentation-guide.md) |

The CDDL/GPLv2 position on redistributing a binary ZFS module is recorded in
[`docs/licensing.md`](./docs/licensing.md). It is not legal advice; read it
before redistributing this image or basing a downstream image on it.

## References

- `Danathar/aurora-zfs-simple`: https://github.com/Danathar/aurora-zfs-simple (simpler approach to the same problem)
- `ublue-os/brew`: https://github.com/ublue-os/brew
- OpenZFS releases: https://github.com/openzfs/zfs/releases

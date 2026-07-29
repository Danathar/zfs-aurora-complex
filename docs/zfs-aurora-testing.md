# ZFS On Aurora Testing Design

If a term is unfamiliar, check the shared glossary first:
[`docs/glossary.md`](./glossary.md)

## Purpose

This repository is a controlled testbed for ZFS support on Aurora using a native `Containerfile` build.

The objective is to validate that we can safely:

1. track the current Aurora/Fedora kernel stream
2. build ZFS kernel modules against the primary kernel the image is expected to boot first
3. install those modules into the final bootc image
4. fail in the GitHub Actions workflow run before a broken image replaces `latest`

## Constraints And Context

1. Aurora is an ostree/bootc-style image, so ZFS integration must happen during image build.
2. ZFS compatibility can lag new Fedora kernels.
3. Branch testing must not overwrite `latest`.
4. pull request (PR) validation should exercise the real build logic but should not push anything.
5. pull request validation stays read-only, but human-owned branch builds may seed or refresh the shared akmods cache when a new target kernel requires it.

## Artifact Strategy

### Main Artifacts

1. candidate OS image: `ghcr.io/danathar/zfs-aurora-complex:candidate-<sha>-<fedora>`
2. stable OS image: `ghcr.io/danathar/zfs-aurora-complex:latest`
3. stable audit tag: `ghcr.io/danathar/zfs-aurora-complex:stable-<run>-<sha>`
4. shared akmods cache image: `ghcr.io/danathar/zfs-aurora-complex-akmods:main-<fedora>`
   - final image builds consume the digest-pinned form: `ghcr.io/danathar/zfs-aurora-complex-akmods@sha256:<digest>`

### Branch Artifacts

1. human-authored branch image: `ghcr.io/danathar/zfs-aurora-complex:br-<branch>-<fedora>`
2. bot-authored branch runs stop after local validation and do not push any public tag
3. shared akmods cache stays the same shared source image; branch builds do not publish branch-specific cache tags, but they may refresh that shared source when it does not yet cover the current primary kernel

## End-To-End Build Flow

### 1. Detect Base Kernel Stream

The main workflow resolves build inputs in one of two modes:

1. default mode: resolve floating refs to immutable digests and immutable stream tags at run time
2. replay mode: read pinned inputs from [`ci/inputs.lock.json`](../ci/inputs.lock.json)

After resolving the base image, the workflow inspects `/lib/modules` inside the pinned base image so it knows every installed kernel, not just one metadata label.

The repo then makes one explicit policy choice:

1. record all detected kernels in logs and the saved input file
2. choose the newest detected kernel as the supported primary kernel
3. require ZFS support only for that supported kernel
4. use image rollback, not an older bundled kernel inside the same image, as the recovery path

Maintenance note:

1. as of April 23, 2026, kernel-release ordering is implemented in one shared helper at
   [`shared/kernel_release.py`](../shared/kernel_release.py)
2. both CI input resolution and the in-image ZFS install helper use that same helper now
3. this was a refactor to remove drift between two equivalent implementations, not a change to the
   primary-kernel policy described above

### 2. Validate Existing Shared Akmods Cache

Before rebuilding akmods, the GitHub Actions workflow run checks whether the shared cache image can be reused.

That check now uses one direct inspection path:

1. copy the shared cache image into a local Open Container Initiative (OCI) layout
2. unpack the filesystem layers from that local copy
3. inspect the extracted RPM filenames directly, requiring a `kmod-zfs` RPM matching
   both the supported primary kernel **and** the exact OpenZFS patch version this run
   resolved — matching the minor line alone would let an older patch satisfy a newer one
4. only once that content matches, verify the cache image's cosign signature against the
   committed `cosign.pub`

Any of those failing forces a rebuild. The signature check means a cache that looks
correct but was not produced by this repo's own pipeline is treated as a cache miss
rather than trusted.

Separate from cache reuse, every workflow path also clones the resolved
`Danathar/akmods` commit once.

That check exists because:

1. an out-of-date shared cache can hide a broken source ref for a while
2. branch and pull request validation should still prove that the resolved akmods commit SHA is
   fetchable, even when they do not end up rebuilding the cache

What that resolved commit actually is:

1. this repo uses the configured fork, not upstream directly
2. it resolves **one exact commit per run**, and clones only that SHA — but by default that
   commit is discovered by resolving the moving `main` tip at the start of each run, not read
   from a checked-in pin. `AKMODS_UPSTREAM_REF` in `ci/defaults.json` exists to freeze it and
   is empty by default
3. the GitHub Actions workflow run clones that exact commit into `/tmp/akmods` for the current run only
4. so updating that fork **does** change what this repo builds, with no edit here — at the next
   cache rebuild. A run that reuses the cache does not rebuild the modules, so fork changes
   reach the image on the next rebuild rather than immediately

See [`docs/akmods-fork-maintenance.md`](./akmods-fork-maintenance.md) for the full cascade
and for why the `akmods-ref` image label is only reliable on a run that rebuilt.

### 3. Build Shared Akmods Cache When Required

If the cache is missing, out of date, or a manual rebuild is requested, the workflow run:

1. clones the resolved `Danathar/akmods` commit for this run
2. points its target output to `zfs-aurora-complex-akmods`
3. writes the upstream `cache.json` file for the supported primary kernel
4. builds the shared cache image for that supported kernel

Branch note:

- human-owned branch builds can run this same refresh path when they move to a base image whose primary kernel is not yet covered by the shared cache
- pull request validation remains read-only and still fails fast instead of publishing cache changes

### 4. Build Candidate Or Branch Image

The final image build is standard OCI composition now.

The workflow passes build arguments directly into [`Containerfile`](../Containerfile):

1. `BASE_IMAGE`
2. `AKMODS_IMAGE`
3. `IMAGE_REPO`
4. `SIGNING_KEY_FILENAME`

That means there is no generated workspace and no per-run file mutation layer.
`AKMODS_IMAGE_TEMPLATE` is still available as a `Containerfile` fallback for
local builds that do not pass an exact cache image ref. CI passes the digest-pinned
cache ref resolved by the earlier akmods job.

The image also installs [`files/usr/lib/modules-load.d/zfs.conf`](../files/usr/lib/modules-load.d/zfs.conf)
so `systemd-modules-load` loads the ZFS kernel module during boot. That makes
post-boot validation report both the userspace and kernel-module versions
without requiring a manual `modprobe zfs`.

### 5. Sign Published Tags

Candidate tags are signed after push by resolving the pushed tag to a digest and
then signing that digest, inside the `production-signing` environment that only
`main` refs can reach.

Stable `latest` is promoted by copying the already-signed candidate digest, not
by signing a second time.

Branch note:

- branch runs cannot sign: the key is environment-scoped to `main`
- human-authored branch runs push an UNSIGNED `br-*` test image via an explicit
  `allow_unsigned` opt-in -- usable only on fresh, never-enforced throwaway VMs,
  since enforced machines refuse unsigned tags under this repository's policy
- automation accounts such as Renovate stop before the push entirely

### 6. Promote Candidate To Stable

Promotion only copies the tested candidate digest to:

1. `latest`
2. `stable-<run>-<sha>`

The candidate signature carries over because both promoted tags resolve to the
same digest. The signer uses legacy cosign attachment storage so bootc's current
containers/image policy path can discover the signature during `bootc upgrade`.

## Why This Repo Is Easier To Reason About

1. no generated workspace layer
2. no recipe mutation
3. no second image repository for candidate
4. no candidate/stable repo-policy normalization inside the image
5. no host repair script for dual repository trust drift

## What Is Still Intrinsically Hard

1. Fedora kernel timing vs OpenZFS release timing
2. shared akmods cache rebuild rules
3. deciding when the primary-kernel-only contract is acceptable

Those are the real complexity drivers that remain.

# Building Locally

If a term is unfamiliar, check the shared glossary first:
[`docs/glossary.md`](./glossary.md)

## Purpose

How the native image build works and how to run it yourself with `podman`,
plus what to change if you fork this repository onto a different base image.
Local builds are for iteration only -- they are never signed and no `bootc`
policy trusts them.

## Native Build Flow

At a high level, `Containerfile` starts from `ghcr.io/ublue-os/aurora-dx`,
`build_files/build-image.sh` installs ZFS RPMs (Red Hat Package Manager
package files) from the shared akmods cache image and writes signing policy,
`bootc container lint` validates the result, and the image is then re-layered
into content-addressed chunks with [Chunkah](https://github.com/coreos/chunkah)
before it is pushed and signed. The ZFS install step inspects every detected
kernel, treats only the newest as the supported primary kernel, and installs
just that kernel's `kmod-zfs` package — older bundled kernels are not treated
as supported ZFS targets, matching the recovery policy above. For the full
build steps, the Fedora-version detection details, and the Chunkah rechunk
mechanics, see ["Input Resolution"](docs/architecture-overview.md#1-input-resolution) through
["Content-Based Layering With Chunkah"](docs/architecture-overview.md#content-based-layering-with-chunkah) in the
architecture overview. The install logic itself lives in
[`containerfiles/zfs-akmods/install_zfs_from_akmods_cache.py`](../containerfiles/zfs-akmods/install_zfs_from_akmods_cache.py).

## Local Build

CI uses [`.github/actions/build-native-image`](../.github/actions/build-native-image/action.yml), which calls `buildah build` directly with the same flags shown below. For local iteration you can invoke `podman build` directly against the repository root. `AKMODS_IMAGE` is the only build argument that is genuinely required outside CI, because the shared akmods cache image is the source of the `kmod-zfs` RPM for the primary kernel.

```bash
podman build \
    --build-arg BASE_IMAGE=ghcr.io/ublue-os/aurora-dx:stable \
    --build-arg AKMODS_IMAGE=ghcr.io/danathar/zfs-aurora-complex-akmods:main-44 \
    -t zfs-aurora-complex:local \
    .
```

Notes:

1. the `AKMODS_IMAGE` tag must match the Fedora major version of the chosen base image; inspect the base image (`skopeo inspect docker://<base>`) to confirm which `main-<fedora>` tag to reference. CI uses the digest-pinned form of that same cache image.
2. `AKMODS_IMAGE` can be omitted for offline experiments; the install helper falls back to `AKMODS_IMAGE_TEMPLATE` and auto-detects the Fedora version from the base image, but that fallback still requires network access to pull the cache image
3. local builds do not go through the candidate-before-promote flow or signing; the resulting image tag is ephemeral and is not trusted by any `bootc` policy

For reproducing a specific published image, prefer the CI workflow with `use_input_lock=true` (see [`ci/inputs.lock.json`](../ci/inputs.lock.json)) rather than a local `podman build`. The lock file pins the base image ref, the build container ref, and the OpenZFS version (line plus, if set, the exact patch) from a prior run. It deliberately does **not** pin the akmods fork commit — that comes from `ci/defaults.json` so there is one source of truth — and it does not record the kernel set, which is re-derived from the pinned base image. Replay is therefore close to, but not the same as, a bit-for-bit reproduction.

## Changing The Base Image

If you clone this repository and want it to build from a different upstream base image, change these files:

1. [`ci/defaults.json`](../ci/defaults.json)
   - update `DEFAULT_BASE_IMAGE`
   - this is the default base image used by the GitHub Actions workflows
2. [`Containerfile`](../Containerfile)
   - update the fallback `ARG BASE_IMAGE`
   - this keeps local `podman build` runs aligned with CI defaults
3. [`README.md`](../README.md) and any other docs/examples that mention the old base image
   - update example `BASE_IMAGE` arguments and descriptive text so the docs match the build

If you use workflow replay mode with `use_input_lock=true`, also check [`ci/inputs.lock.json`](../ci/inputs.lock.json). That lock file can pin one exact base image for a specific replayed run even after the normal defaults have changed.

# Building Locally

If a term is unfamiliar, check the shared glossary first:
[`docs/glossary.md`](./glossary.md)

## Purpose

How the native image build works and how to run it yourself with `podman`,
what a fork needs to set up before its own workflows will pass, plus what to
change if you fork this repository onto a different base image.
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

## Running The Workflows In A Fork

Forking this repository and letting its GitHub Actions workflows run in your
own namespace needs one setup step that is easy to miss: **the shared akmods
cache package must be public.**

The workflows publish the pre-built ZFS kernel modules to a GitHub Container
Registry (GHCR) package of their own, named by `AKMODS_REPO` in
[`ci/defaults.json`](../ci/defaults.json) — for this repository,
`ghcr.io/danathar/zfs-aurora-complex-akmods`. GitHub creates a brand-new GHCR
package as **private**, and two things then read that package with no
credentials at all:

1. the pull request validation workflow's cache check
   ([`ci_tools/prepare_validation_build.py`](../ci_tools/prepare_validation_build.py)),
   which has no registry login and no `packages` permission
2. the image build itself
   ([`containerfiles/zfs-akmods/install_zfs_from_akmods_cache.py`](../containerfiles/zfs-akmods/install_zfs_from_akmods_cache.py)),
   which runs `skopeo copy` from *inside* the build container, where no
   runner-side registry login reaches it

So a private akmods package does not merely degrade cache reuse — it stops the
image from building.

That also makes bootstrapping a fork a chicken-and-egg problem, because the
package does not exist until a run publishes it, and you cannot change the
visibility of a package that is not there yet. **Expect it to take two runs, the
first of which fails.**

1. run the main workflow (**Build And Promote Main Image**) with
   `rebuild_akmods=true`. **This run is expected to fail, and that does not mean
   your fork is misconfigured.** *Build Shared ZFS Akmods Cache* publishes the
   new cache package and *Sign Shared ZFS Akmods Cache* signs it — both
   authenticate, so both succeed — and then *Build Candidate Image* fails,
   because the in-image `skopeo copy` cannot read the package the same run just
   created
2. make the now-existing package public:
   > Your profile or organization → **Packages** → the `*-akmods` package →
   > **Package settings** → **Change visibility** → **Public**
3. re-run the workflow. The cache check now finds the cache that run 1 published
   *and signed*, so it reuses it rather than rebuilding — the second run skips
   the expensive kernel-module build entirely

Notes:

1. this applies to forks running their own CI. Pull requests opened *against*
   this repository are unaffected — see
   ["Why Pull Requests Against This Repository Are Unaffected"](#why-pull-requests-against-this-repository-are-unaffected)
   below for why, and for the evidence behind that claim
2. symptoms of missing this step are registry permission errors — a
   `403 Forbidden` bearer-token failure from `skopeo` — not "cache not found"
   errors. Rebuilding the cache will not clear them
3. the published *image* package has the same default. If you intend anyone
   (including your own machines running `bootc upgrade`) to pull the image
   without logging in, that package needs to be public too

### Why Pull Requests Against This Repository Are Unaffected

This is what keeps the private-package problem a fork-only concern rather than
something every outside contributor would trip over, so it is worth recording
why it holds — and how that was actually checked.

A `pull_request` run executes in the **base** repository's context, even when
the pull request comes from a fork. `GITHUB_REPOSITORY_OWNER` is therefore
`Danathar`, not the contributor's account, so
[`ci_tools/tagging_context.py`](../ci_tools/tagging_context.py) resolves
`image_org` to `danathar` and the cache check reads this repository's
already-public package. The contributor's own GHCR packages are never
consulted, and their visibility does not matter.

GitHub's own documentation does not state this. The
[variables reference](https://docs.github.com/en/actions/reference/workflows-and-actions/variables)
defines `GITHUB_REPOSITORY` as "the owner and repository name" without
addressing the fork case at all, and the secure-use guide covers
`pull_request_target` rather than plain `pull_request`. It was confirmed
empirically instead (checked 2026-08-04):

1. `cli/cli` run `30837915533` is a `pull_request` event whose
   `head_repository` is the fork `zwick/cli` while the run's `repository` is
   the base `cli/cli`
2. in that run's logs, `actions/checkout` resolved `repository: cli/cli` and
   reported `Syncing repository: cli/cli`
3. that input's default is `${{ github.repository }}` (see `action.yml` in
   `actions/checkout`), so `github.repository` — and with it
   `GITHUB_REPOSITORY_OWNER` — was the base repository, not the fork
4. separately, `ublue-os/bluefin` run `28942859343`, a pull request from the
   fork `bashilias/bluefin`, ran the workflow file from the **base**
   repository, which is consistent with base-repo context throughout

One honest limitation: this repository has never actually received a pull
request from a fork — every pull request in its history came from a same-repo
branch. So the behavior above is confirmed by mechanism on other repositories,
not observed here. Its own run logs cannot settle the question, because with
the base owner and head owner identical, an `image_org=danathar` line is
consistent with either interpretation. Do not treat one of this repository's
own pull request runs as evidence for this.

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

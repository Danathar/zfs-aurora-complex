# Safety Model And Recovery Policy

If a term is unfamiliar, check the shared glossary first:
[`docs/glossary.md`](./glossary.md)

## Purpose

What this repository promises about published images, what it deliberately
does not promise, and what an operator should do when a published image turns
out to be bad. Read this before depending on `:latest`.

## ZFS Line Policy

`DEFAULT_ZFS_MINOR_VERSION` in [`ci/defaults.json`](../ci/defaults.json) is currently **2.3**,
deliberately, even though 2.4 exists and builds fine against the current kernel.

The reason is rollback safety rather than caution about 2.4 itself. The maintainer's production
pools run the 2.3 line. Publishing an image on 2.4 would mean a machine switching to this image
crosses a ZFS line boundary, and once any new 2.4 on-disk feature is activated on a pool, the
previous image can no longer import that pool -- so rollback, which is this repo's entire
recovery model (see below), would produce a system that boots correctly and cannot read its
data.

Holding at 2.3 keeps the switch to this image a *single* change: same ZFS as before, so
rollback is unconditionally safe, and the only thing being tested is the image and pipeline.

Moving to 2.4 is a separate, deliberate decision to be taken later, on its own merits. Renovate
will keep opening a 2.3 -> 2.4 pull request; it is configured never to automerge it, and it
should not be merged until the pool-feature consequences are accepted. Both lines currently
declare the same kernel ceiling (`Linux-Maximum: 7.0`), so staying on 2.3 costs no kernel
currency today.

## Safety Model

Stable users should only see tested outputs. Scheduled runs first check
whether the upstream Aurora base image has advanced, or a newer OpenZFS patch
is available on the configured minor line, since the last promoted image, and
skip the rest of the workflow only if neither has changed; push and manual
runs always build. Once a run
does build, it resolves and pins its inputs, reuses or rebuilds the shared
akmods cache, builds and signs a candidate image, and only then promotes that
candidate digest to an audit tag and `latest`. If candidate fails, `latest`
does not move. See ["Scheduled-Build Gate"](docs/architecture-overview.md#0-scheduled-build-gate)
and ["Promotion And Signing"](docs/architecture-overview.md#5-promotion-and-signing) in the
architecture overview for the full decision tables and promotion ordering.

Published images must be signed, matching the Universal Blue image-template
model. The repository commits only `cosign.pub`; the matching private key must
be stored as the GitHub Actions secret `SIGNING_SECRET`. The publish action
refuses to push an image when that secret is missing.

Initial signing setup:

```bash
COSIGN_PASSWORD="" cosign generate-key-pair
gh secret set SIGNING_SECRET < cosign.key
git add cosign.pub
git commit -m "Configure image signing key"
git push
```

Never commit `cosign.key`. For key rotation and the full signing model, see
[`docs/signing-and-bootc.md`](./signing-and-bootc.md).

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

Everything lives in two GitHub Container Registry (GHCR) repositories: the OS
image (`ghcr.io/danathar/zfs-aurora-complex`, with `latest`, `candidate-*`,
`stable-*` audit, and `br-*` branch tags) and the shared akmods cache image
(`ghcr.io/danathar/zfs-aurora-complex-akmods`). There is no separate
candidate repository, branch-scoped akmods alias repo, or stable-vs-candidate
repair script to keep in sync. The akmods cache is signed the same way the OS
image is, and a cache that fails signature verification is treated as a
rebuild, not reused. For the full tag list and how transient
`*-unsigned-<run_id>` tags fit into publishing, see
["Outputs"](docs/architecture-overview.md#outputs) in the architecture overview.

# Safety Model And Recovery Policy

If a term is unfamiliar, check the shared glossary first:
[`docs/glossary.md`](./glossary.md)

## Purpose

What this repository promises about published images, what it deliberately
does not promise, and what an operator should do when a published image turns
out to be bad. Read this before depending on `:latest`.

## The ZFS Line Is Set By The Base Image, Not By This Repo

`DEFAULT_ZFS_MINOR_VERSION` in [`ci/defaults.json`](../ci/defaults.json) selects which OpenZFS
line the *kernel module* is built from. It does **not** freely select the ZFS line the image
ships, because the Aurora DX base image already contains ZFS **userspace** packages, and those
constrain what can be installed on top.

Established empirically on 2026-07-28 by attempting to move this repo to the 2.3 line
(pull request #54, closed unmerged). The akmods build succeeded -- 2.3.8 compiled cleanly
against kernel 7.0.12-201.fc44, consistent with its `Linux-Maximum: 7.0` -- and the image build
then failed:

```text
Problem 1: installed package libzfs7-2.4.3-1.fc44.x86_64 obsoletes libzfs6 <= 2.4.3
           provided by libzfs6-2.3.8-1.fc44.x86_64 from @commandline
Problem 2: installed package libzpool7-2.4.3-1.fc44.x86_64 obsoletes libzpool6 <= 2.4.3
           provided by libzpool6-2.3.8-1.fc44.x86_64 from @commandline
```

The base carried `libzfs7`/`libzpool7` from the 2.4 line. Those *obsolete* the 2.3-era
`libzfs6`/`libzpool6`, so `dnf5` refused the transaction.

**What follows from this:**

1. Changing `DEFAULT_ZFS_MINOR_VERSION` to a line *older* than the base image's own ZFS
   userspace will fail at image-build time, not at akmods-build time. A green akmods job is not
   evidence the line change will work.
2. This repo therefore cannot hold an older ZFS line to match an existing machine while also
   tracking a current Aurora base. Those two goals conflict, and the base wins.
3. A machine moving onto this image from an older ZFS line **must** cross that line. That is not
   avoidable by configuration here. The mitigation is the rollback discipline described below:
   keep the previous image as a pinned rollback target, and do not run `zpool upgrade` until the
   new line is trusted. Importing a pool created under an older line does not activate new
   feature flags by itself -- only `zpool upgrade` does.

Two workarounds were considered and rejected: removing the base's ZFS packages before installing
(diverges from Aurora's own stack and needs re-checking on every base update), and pinning an
older base image (surrenders the kernel currency this repo exists to track).

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
rebuild, not reused.

One deliberate exception to "everything is signed": `br-*` branch tags are
**unsigned test images**. Branch refs cannot reach the signing key (it is
scoped to a `main`-only environment), so human-authored branches publish via an
explicit unsigned opt-in instead. Machines enforcing this repository's
signature policy refuse those tags; they are only usable on fresh, throwaway
test VMs and must never be a durable install -- see
["Testing an unsigned branch image"](./install-and-verify.md) for the rules.
For the full tag list and how transient `*-unsigned-<run_id>` tags fit into
publishing, see ["Outputs"](./architecture-overview.md#outputs) in the
architecture overview.

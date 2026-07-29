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

## Live Pool State: Do Not Run `zpool upgrade`

The maintainer's production host switched onto this image on 2026-07-29, coming from
`aurora-zfs-simple` running the ZFS **2.3** line. This image ships **2.4.3**, and per the
section above that crossing is unavoidable -- the base image dictates the userspace line.

As a result `zpool status` reports something like *"Some supported features are not enabled on
the pool"*. **That warning is expected and must be left alone.** It is what a 2.3-era pool looks
like when read by 2.4 tooling, and it is precisely what keeps rollback available.

Importing a pool under newer ZFS does **not** activate new feature flags. Only an explicit
`zpool upgrade` does. So while the pools stay un-upgraded:

- the previous image (`aurora-zfs-simple`, pinned as the rollback target) can still import them
- `bootc rollback` remains a complete recovery path

The moment `zpool upgrade` runs, that stops being true permanently: the older image can no
longer import those pools, and rollback yields a system that boots correctly and cannot read its
data. There is no undo.

**Rule:** do not run `zpool upgrade` -- and do not "fix" the `zpool status` warning -- until the
2.4 line is trusted and rolling back to a 2.3 image is no longer a recovery path anyone wants.
That is a deliberate, one-way decision, not routine maintenance.

`bootc rollback` itself was exercised successfully in a test VM on 2026-07-29, with
`aurora-zfs-simple` as the rollback target -- the same arrangement as the production host. So
the *mechanics* of the recovery path are verified rather than assumed.

Note precisely what that does and does not establish. It proves rollback works. It does not
independently prove that 2.3-line tooling can still import the production pools after they have
been read by 2.4 -- that rests on the OpenZFS feature-flag guarantee (features activate only on
an explicit `zpool upgrade`, never on import) plus the discipline above. The guarantee is sound;
it is also the only thing standing between a working rollback and an unreadable pool, which is
why the rule is stated as absolutely as it is.

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
model. The repository commits only `cosign.pub`; the matching private key is
stored as `SIGNING_SECRET` in the **`production-signing` GitHub Environment**,
restricted to the `main` branch. The publish action refuses to push an image
when that secret is missing.

Store it as an *environment* secret, not a repository secret. A repository
secret is readable by every workflow run in the repo, including
`build-branch.yml` runs triggered by pushing any branch -- so a leaked
write-scoped token could sign images with it. GitHub grants environment
secrets based on the ref a job actually runs against, which a branch push
cannot forge.

Initial signing setup:

```bash
COSIGN_PASSWORD="" cosign generate-key-pair

# One-time, in the web UI: Settings -> Environments -> New environment ->
# `production-signing` -> Deployment branches -> "Selected branches and tags"
# -> Add deployment branch or tag rule -> Branch -> `main`
gh secret set SIGNING_SECRET --env production-signing < cosign.key

git add cosign.pub
git commit -m "Configure image signing key"
git push
```

Only `main` jobs that declare `environment: production-signing` can read it
(`build-candidate-image` and `sign-akmods-cache` in `build.yml`). Branch runs
deliberately cannot sign at all; they publish unsigned `br-*` test images
instead, and cannot rebuild the shared akmods cache.

Never commit `cosign.key`. Keep the original file somewhere safe: GitHub
secrets cannot be read back, so it is the only way to re-add or migrate the
key. For key rotation and the full signing model, see
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

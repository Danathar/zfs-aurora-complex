# Akmods Fork Maintenance

If a term is unfamiliar, check the shared glossary first:
[`docs/glossary.md`](./glossary.md)

## Purpose

This page explains how this repository chooses an upstream akmods source ref,
when to use a temporary pin, and when to let floating tracking heal itself.

Current control points:

- checked-in defaults file [`ci/defaults.json`](../ci/defaults.json)
- manual environment overrides when you need one-off validation

## How The Akmods Ref Is Chosen

The repo supports three resolution modes, checked in order:

1. **Explicit environment override.** If `AKMODS_UPSTREAM_REF` or `DEFAULT_AKMODS_REF` is set in the process environment, that value wins. This is the escape hatch for debugging a specific upstream commit.
2. **Explicit pin in `ci/defaults.json`.** If the `AKMODS_UPSTREAM_REF` field in [`ci/defaults.json`](../ci/defaults.json) is non-empty, that commit is used. This is how you freeze the repo to a known-good SHA during an outage.
3. **Floating tracking ref.** Otherwise, `AKMODS_UPSTREAM_TRACK` (default `"main"`) is resolved via `git ls-remote` against `AKMODS_UPSTREAM_REPO` at the start of every run, and the resulting SHA is pinned for the rest of the run.

The floating mode is the self-healing default. A transient upstream mismatch stays red until upstream lands a compatible commit, at which point the next cron run picks it up without any human edit.

Every build records the resolved commit SHA in two places:

1. the workflow `build-inputs` manifest artifact, so you can trace any run to the SHA it used
2. an OCI image label `org.zfs-aurora-complex.akmods-ref`, so any published candidate or stable image can be traced back to the exact upstream commit it was built from — even after the tracking ref has moved

## Why A Pin Still Exists At All

- **Reproducibility of a specific run.** Use `ci/inputs.lock.json` replay mode to rebuild an exact prior input set, or set `AKMODS_UPSTREAM_REF` explicitly to replay a specific upstream commit.
- **Debugging.** Pinning short-term isolates the akmods side while you chase a build failure.
- **Emergency freeze.** If upstream lands a change you actively do not trust, setting the `ci/defaults.json` pin freezes the repo to the last known-good SHA until you choose to unfreeze it.

## When You Should (And Should Not) Bump The Pin

You usually should **not** bump this pin on a cadence.

The daily `build.yml` cron re-resolves base image, kernel set, and akmods cache on every run. A red build caused by a temporary upstream mismatch (new Fedora kernel, no matching OpenZFS release yet) is expected and self-heals once upstream catches up — the stable tag does not move while the candidate is red, so users see the last known-good image. See [`docs/upstream-change-response.md`](./upstream-change-response.md) for the full decision table.

Setting or moving an explicit `AKMODS_UPSTREAM_REF` is the right move only when:

1. a newer akmods fork commit is known to support the current Fedora kernel, and waiting for the floating tracking ref will not pick it up
2. you need to reproduce or debug a specific failing build against an exact upstream SHA
3. the fork made a breaking change (cache layout, publish naming, dependency set) that requires freezing the build to a known-good SHA while you adapt

Otherwise: leave it alone and let the cron retry.

## Update Process

Under the floating-ref default, there is no routine update process — the cron resolves `AKMODS_UPSTREAM_TRACK` every run.

If you do need to pin (to debug, to freeze during an upstream outage, or to reproduce a specific past build):

1. inspect the current state of `Danathar/akmods`
2. choose the exact commit you want to test
3. set `AKMODS_UPSTREAM_REF` in [`ci/defaults.json`](../ci/defaults.json) to that SHA
4. run branch or manual validation first if the change is risky
5. merge only after `main` builds and signs successfully
6. when the outage clears, set `AKMODS_UPSTREAM_REF` back to `""` so the floating ref resumes

## Syncing The Fork With Upstream

The two processes above are about which fork commit this repo *consumes*. This
section is about maintaining the fork itself: pulling new commits from
`ublue-os/akmods` (the `upstream` remote) into `Danathar/akmods` so the floating
`main` ref keeps gaining upstream ZFS and build fixes.

Because `main` floats by default, a fork sync is what feeds new upstream work
into this repo's next build. Sync periodically, and especially when a new Fedora
kernel needs an upstream ZFS compatibility fix.

A sync is **not** a clean fast-forward. The fork carries local customizations
that this repo depends on, and they collide with upstream changes in the same
files. Procedure:

1. in the `Danathar/akmods` checkout, fetch both remotes:
   `git fetch upstream && git fetch origin`
2. branch from the fork tip: `git switch main && git pull && git switch -c sync/upstream-main`
3. merge upstream: `git merge upstream/main` (expect a conflict, see below)
4. resolve, validate, then merge the branch into `main` and push the fork only
   after validation passes

### Known Conflict: `Justfile`

The fork's load-bearing customizations live in the `Justfile` and conflict with
upstream every time upstream touches the same variable block. Resolve by
**combining**, never by taking one side wholesale:

1. keep the fork's `akmods_name := env('AKMODS_IMAGE_NAME', shell(yq + ' "...name" images.yaml', ...))`
   line. This derives the published image name from `images.yaml` `.name`, which
   [`ci_tools/akmods_configure_zfs_target.py`](../ci_tools/akmods_configure_zfs_target.py)
   sets per run. Taking upstream's hardcoded `akmods-<target>` instead would make
   the cache publish under the wrong name and silently break cache reuse here.
2. adopt upstream's `yq` variable form (`shell(yq + ' ...')`) for every
   `images.yaml` read, including the `akmods_name` line above. Upstream's
   `yq := 'yq --yaml-fix-merge-anchor-to-spec'` is required by newer yq for the
   merge anchors in `images.yaml`; this repo's configure step uses the same flag.
3. preserve the fork's OpenZFS-release-discovery token hardening (the
   `--secret=id=github_token` plumbing in the `build`/`test` recipes and the
   xtrace/JSON-array guards in `build_files/zfs/build-kmod-zfs.sh`).

### Validate The Sync

Before pushing the fork:

1. no conflict markers remain: `grep -rn '^<<<<<<<\|^>>>>>>>' .`
2. the `Justfile` parses and resolves: `AKMODS_KERNEL=main AKMODS_VERSION=<fedora> AKMODS_TARGET=common just --evaluate` succeeds
3. with the zfs target configured (as this repo does), `akmods_name` resolves to
   the cache repo name, not `null`
4. `bash -n build_files/zfs/build-kmod-zfs.sh` passes
5. after pushing, run this repo's `build-branch` workflow (or a manual `build.yml`
   with `rebuild_akmods=true`) so a real akmods build validates the synced fork
   before `main` floats onto it

## What Usually Forces A Temporary Pin

1. new Fedora kernel behavior where the tracking ref is not yet usable but a known-good commit is available
2. upstream changes around cache layout, dependency lists, or image publishing that need isolated validation
3. changes required for future Fedora majors

## What To Validate After Changing A Pin

1. `Build Shared ZFS Akmods Cache` still succeeds
2. shared cache image contains the `kmod-zfs` RPM needed for the supported primary kernel
3. final candidate image still installs ZFS userspace and modules correctly
4. promotion and signing still succeed on `main`

## Plain-Language Model

The moving pieces are:

1. the configured fork repository: `Danathar/akmods`
2. the tracking ref or explicit pin configured in [`ci/defaults.json`](../ci/defaults.json)
3. the resolved commit SHA, meaning the exact Git commit ID used for one run
4. the temporary clone the workflow run creates in `/tmp/akmods`

How they relate:

1. the workflow run still uses the configured fork as the source repository
2. the workflow run resolves the selected ref to one exact commit SHA before cloning
3. the workflow run uses that resolved commit, not a branch that can move mid-build
4. that clone in `/tmp/akmods` exists only for the current run
5. when the run ends, that temporary checkout is discarded

Most important consequence:

- when the floating `AKMODS_UPSTREAM_TRACK` ref is active (default), pushing a new commit to `Danathar/akmods` on that ref becomes the source for the next build
- when `AKMODS_UPSTREAM_REF` is set in `ci/defaults.json` or the environment, pushing new upstream commits does **not** change this repo's builds until that pin is cleared or moved

## Failure Discipline

If a new akmods pin breaks the build, revert the pin first.
Do not start patching unrelated workflow code until you know the akmods change is really required.

## Important Current Assumption

This repository no longer patches the cloned akmods `Justfile` at runtime.

That means:

1. if this repo needs repo-specific publish-name behavior, that logic must exist in the pinned `Danathar/akmods` commit itself
2. the clone step here is intentionally boring on purpose: clone, check out the exact commit, verify the commit SHA, stop
3. if a future akmods change breaks repo-specific publishing, fix the fork and repin it instead of reintroducing a local patch layer

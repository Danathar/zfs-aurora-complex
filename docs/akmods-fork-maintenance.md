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

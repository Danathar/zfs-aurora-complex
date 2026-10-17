---
description: Reproduce a specific past build with pinned inputs, to diagnose it
mode: agent
---

# Replay a build

**Goal:** rebuild with the pinned inputs a past run used, so a difference in
behavior is attributable to something other than those inputs.

**This is a partial replay, not an exact one, and the difference matters.** The
lock file pins the base image, the build container and the ZFS
version. It does not pin the ZFS kernel modules. Those come from the
shared akmods cache image, and the cache check reuses whatever cache matches
the kernel release and ZFS version, whichever akmods commit built it. The
akmods source commit matters only when the cache is rebuilt, and it is not
pinned by default either: `AKMODS_UPSTREAM_REF` is empty, so
`_resolve_default_akmods_ref()` resolves the tracked `main` ref afresh on every
run. Either way the replay can ship modules built from different source than
the run you are reproducing. Say so in the result rather than concluding from a
non-reproduction that the cause was not in the inputs.

Read this before starting: replay mode is reachable **only** from a manual
`workflow_dispatch`, so no push or pull request ever exercises it — it is an
incident-reproduction tool, and a broken guard in it surfaces exactly when
someone needs it. Treat its refusals as
informative, not as obstacles.

## 1. Get the inputs of the run you are reproducing

Every run uploads a `build-inputs` artifact written by
`ci_tools/write_build_inputs_manifest.py`.

```bash
gh run download <run-id> --name "build-inputs-<run-id>"
cat build-inputs.json
```

The artifact name carries the run id (`build-inputs-${{ github.run_id }}` in
`.github/actions/prepare-main-akmods/action.yml`), so a bare `build-inputs`
will not resolve. `gh run view <run-id> --json ...` or the run page lists the
exact name if you are unsure.

The `inputs` object holds everything the lock file needs. If the artifact is
missing, the run predates it or failed before that step — say so rather than
reconstructing the values by hand from logs.

## 2. Fill in the lock file

`ci/inputs.lock.json` ships with `REPLACE_ME` placeholders. Copy from
`build-inputs.json`:

| Lock field | From `build-inputs.json` |
| --- | --- |
| `base_image` | `inputs.base_image_pinned` — the digest form, not the tag |
| `build_container` | `inputs.build_container_pinned` |
| `zfs_minor_version` | `inputs.zfs_minor_version` |
| `zfs_version` | `inputs.zfs_version` |

**`zfs_version` is the one people leave empty and should not.** Left empty, the
resolver re-resolves the newest release on `zfs_minor_version`, so once a newer
patch exists the replay does not reproduce the original ZFS version — and the
whole point was to hold the inputs still.

`akmods_upstream_ref` is not in the checked-in lock file, and this table does
not ask you to add it. The resolver does read it: a lock that carries
`akmods_upstream_ref` uses that commit ahead of every other source
(`resolve_configured_inputs()` in `ci_tools/resolve_build_inputs.py`). It does
not stand in for the fallback, though: the resolver works out the configured ref
(`_resolve_default_akmods_ref()`) before it opens the lock, so a replay whose
tracking ref has been deleted, or whose `git ls-remote` fails, still stops there
even when the lock carries a commit. The
template stays without it because a value left there would override
`ci/defaults.json` on every later replay. Copying `inputs.akmods_upstream_ref`
into a filled-in lock would not make the replay exact, for two reasons:

- It is the commit that run *resolved*, not necessarily the one that built its
  modules. On a cache-reuse run the modules came from an earlier cache build.
  The accurate record is the `org.zfs-aurora-complex.akmods-image` label on the
  published image; see the warning in
  [`akmods-fork-maintenance.md`](../../docs/akmods-fork-maintenance.md).
- A replay that reuses the shared cache never builds from the commit at all.
  `ci_tools/check_akmods_cache.py` decides reuse from the kernel release, the
  ZFS version and the signature.

The input a replay really leaves unpinned is the akmods cache image. Pinning it
would change the cache check, which
[`risk-tiers.md`](../../docs/risk-tiers.md) lists as Tier 3, so it is a
maintainer decision, not a lock-file edit.

## 3. Expect the build-container guard to fire

If `DEFAULT_BUILD_CONTAINER_IMAGE` in `ci/defaults.json` has moved since the run
you are replaying, `resolve_build_inputs` refuses with a message naming both
refs. That guard is correct: the container it would actually run in is not the
one the lock file names, so the replay would not be a replay.

The message tells you the resolution — set `DEFAULT_BUILD_CONTAINER_IMAGE` to
the locked ref *and* the matching literal in each workflow's `container:` block,
through a reviewed pull request. That container runs `--privileged` as root with
`/` mounted, which is why it cannot be overridden from a dispatch input.

Do not work around this by editing the lock file to match the current container.
That produces a green run that proves nothing.

**And be aware what the prescribed fix costs before proposing it.** Changing
`DEFAULT_BUILD_CONTAINER_IMAGE` in `ci/defaults.json` plus the workflow
`container:` literals is a change to `main`, and a push to `main` starts
`build.yml`. That run is not a `workflow_dispatch`, so the promotion condition is
satisfied and it will build, sign and move `:latest` using the *historical*
privileged container — before the diagnostic replay is ever dispatched. That is
a production change made in service of an investigation, and it needs an
explicit maintainer decision, not a step in a runbook.

## 4. Stop here if you are an agent

Everything above is preparation: read the artifact, fill in the lock, work out
whether the build-container guard will fire. **The dispatch itself is the
maintainer's.** It starts the production workflow, and AGENTS.md section 0 rule
6 puts that out of an agent's hands. Hand over the filled-in lock, the command
you would run, and the constraints below.

## 5. For the maintainer: what dispatching actually costs

The lock file must be on the ref the run checks out. `gh workflow run` without
`--ref` runs the default branch's copy, which still has `REPLACE_ME` in
`ci/inputs.lock.json` — the run then fails in `resolve_build_inputs`.

**A replay can only run from `main`.** Two separate guards stop a dispatch on
any other ref, such as a `replay/*` branch:

- `build-zfs-akmods` runs only when `github.ref` is `refs/heads/main`, and every
  later job needs it. On another ref the whole build is skipped and nothing is
  built, signed or published.
- `sign-akmods-cache` and `build-candidate-image` declare `environment:
  production-signing`.
  [`production-boundary-proposal.md`](../../docs/production-boundary-proposal.md)
  records that environment as restricted to `main` (adopted 2026-07-29).

So the populated lock has to reach `main` through a reviewed pull request before
the dispatch. Whether a diagnosis is worth that is a decision, not a runbook
step.

```bash
gh workflow run build.yml \
  --ref main \
  -f use_input_lock=true \
  -f lock_file=ci/inputs.lock.json \
  -f promote_to_stable=false
```

**`promote_to_stable=false` is not optional for a diagnostic replay.** The
default is `true`. A replay that promotes moves `:latest` to an image built for
diagnosis, which `bootc upgrade` then pulls onto whatever is tracking it.

**It does not make the replay read-only, though.** `promote_to_stable=false`
suppresses the promotion job and nothing else. `build.yml` calls
`prepare-main-akmods` without overriding `allow_cache_rebuild`, whose default is
`true`, so if the locked kernel and ZFS inputs do not match what the shared cache
currently holds, the replay **rebuilds and republishes the shared
`main-<fedora>` akmods tag** before building its candidate. That is a production
supply-chain input, replaced by a run you were treating as diagnostic. Check
whether a rebuild would be triggered before dispatching, and get explicit
authorization if it would.

## 6. Report

Say which run you replayed, which inputs the lock held, whether the replay
reproduced the behavior, and — explicitly — whether you promoted. If the replay
did **not** reproduce it, that is a finding: the cause was not in the inputs.

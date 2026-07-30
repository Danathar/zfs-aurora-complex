# Maintenance Watchlist

Things baked into this repo today that are expected to need a human decision later --
not because anything is wrong now, but because the assumption underneath them is
time-bound, version-bound, or a deliberate one-way choice. Renovate (`renovate.json`)
already tracks ordinary version pins; this document is for the pins and decisions it
cannot see or cannot express.

If you fix or resolve one of these, delete the entry rather than marking it done --
this file should only ever describe live watch items.

## How an item ends up here

Not every pin qualifies. An item belongs here only if both are true:

1. **It will eventually need a human decision**, not just a version bump a bot can propose.
2. **No automation will surface that decision on its own.** If Renovate (or CI) already
   opens a PR when this needs attention, it does not belong here -- put a one-line note
   in the relevant doc instead, not a watchlist entry.

## Open items

### The rechunk host's container toolchain is pinned to Ubuntu's `resolute` suite

**Where:** [`.github/actions/prepare-rechunk-host/action.yml`](../.github/actions/prepare-rechunk-host/action.yml)

**What:** `crun`, `buildah`, `podman`, and `skopeo` are installed unconditionally from
Ubuntu's `resolute` apt suite on every job that rechunks with Chunkah, because Chunkah
needs all four to be a matched, sufficiently new set (podman `>= 5` for OCI layer
annotations) and GitHub's hosted runner image cannot be relied on to provide that
combination -- see the incident this fixed: a runner image bumped podman without
bumping crun, an earlier version of this step's own skip-if-new-enough logic missed
that split, and the mismatch broke every rechunk with `crun: unknown version
specified` until fixed.

**Not automated because:** there is no Renovate datasource for "an Ubuntu apt suite
name." Renovate can bump digests and semver tags; it cannot notice that `resolute` has
gone end-of-life or that a `resolute` package update now expects a newer glibc than the
`noble` (24.04) runner host provides.

**What to watch for:**
- `resolute` (Ubuntu 26.04 LTS, released 2026-04-23) reaching end of standard support.
  Not urgent -- LTS support windows run years, and this was not verified against an
  exact EOL date, only confirmed as a released stable suite, not a development one.
- A `resolute` package requiring a newer glibc or libc-adjacent dependency than the
  `noble` host provides. This is the nearer-term risk: it fails at `apt-get install`
  time, before any build step runs, so it is loud rather than silent -- but it can
  happen at any `resolute` point release, not just at EOL.
- GitHub's hosted runner image eventually shipping a matched, current podman+crun+
  buildah+skopeo set by default, at which point the whole `resolute` dependency could
  be dropped. (An earlier version of this step tried to detect that automatically by
  checking podman's version alone; that is exactly what broke. Any future attempt to
  re-automate this needs to *probe* that the toolchain works -- e.g. run a trivial
  container -- not infer it from one component's version number.)

**If it fires:** the build fails at the `Update Podman` step, before anything is built,
pushed, or signed. Re-point the suite name (or replace this step's approach entirely)
and confirm with a real rechunk run.

---

### `ublue-os/remove-unwanted-software` is pinned to a commit with no version tag

**Where:** [`.github/actions/prepare-rechunk-host/action.yml`](../.github/actions/prepare-rechunk-host/action.yml)
(`Maximize build space` step)

**What:** pinned to commit `695eb75bc387dbcd9685a8e72d23439d8686cba6`, which merged a
"v10" feature onto that project's `master` before it had cut a `v10` release tag. The
action's own comment says: *"Renovate will not propose updates for it until upstream
tags one."*

**Not automated because:** Renovate's GitHub Actions manager tracks tagged releases; a
commit pin with no corresponding tag sits outside what it proposes updates for.

**What to watch for:** `ublue-os/remove-unwanted-software` cutting a `v10` tag (or
later). Once that happens, Renovate should start proposing bumps again on its own --
this item can likely be deleted from this list at that point, but confirm a PR actually
appears before assuming so.

**If it fires:** nothing breaks. This is a "silently stops getting updates" risk, not a
"silently breaks" one -- the pin keeps working, it just never moves.

---

### The ZFS-line rollback carve-out is a deliberate one-way decision, not a setting

**Where:** [`docs/safety-model.md`](./safety-model.md), "Live Pool State: Do Not Run
`zpool upgrade`"

**What:** the production host runs OpenZFS 2.4 against pools that have not run
`zpool upgrade`, specifically so the previous image (on the OpenZFS 2.3 line) can still
import them if a rollback is needed. This is not a bug and not something to "fix" --
the `zpool status` warning it produces is expected and must be left alone.

**Not automated because:** this is a judgment call about how much the 2.4 line is
trusted, not a version number a bot can compare. There is nothing to bump.

**What to watch for:** the point where the maintainer judges the 2.4 line trustworthy
enough that losing the 2.3 rollback path is an acceptable trade. That is a decision to
make deliberately, not a threshold to detect.

**If it fires:** running `zpool upgrade` is described in `safety-model.md` as
irreversible -- "There is no undo." This entry exists so that decision gets made on
purpose, not by someone reflexively "fixing" the warning.

## Related, not duplicated here

- **`docs/akmods-fork-maintenance.md`** covers the akmods fork pin itself (float vs.
  pin, sync process). That is a steady-state design, not a ticking clock, so it is not
  repeated here.
- **`docs/production-boundary-proposal.md`**, "Deliberately not done," records the
  decision not to add `main` branch protection, with its own reasoning and its own
  revisit condition. Not duplicated here because that document already carries the
  full context a revisit would need.
- Ordinary version and digest pins Renovate already tracks (Chunkah, the akmods build
  container digest, `ruff`, the OpenZFS minor line) are not watchlist items by
  definition -- see the `customManagers` entries in `renovate.json` for what is already
  covered.

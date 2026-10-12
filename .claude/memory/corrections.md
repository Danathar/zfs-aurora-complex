# Corrections

Newest first. See [`README.md`](README.md) for what belongs here.

---

## Deleting the rechunk host's `resolute` pin once GitHub fixed the trigger

**Believed:** the "Update Podman" step in `prepare-rechunk-host` only exists
because of a GitHub runner regression that has since been rolled back, so once
GitHub fixed it the step can simply be deleted.

**True:** the step supplies a version-matched `crun`/`buildah`/`podman`/`skopeo`
set, and the runner's own noble podman is still 4.9.3. Chunkah relies on OCI
layer annotations that podman before 5 silently drops on push, so deleting the
step reintroduces a *silent* failure rather than a loud one. Reverting #59
restores the old "skip when podman is already >= 5" logic, which is the
inference that broke in the first place (see the next entry), so even that is
a trade made on purpose. GitHub rolled the image back
and confirmed resolution (actions/runner-images#14473), so the specific trigger
is gone, but that is GitHub's rollback, not this repo's fix.

**Established by:** the step's own comment in
`.github/actions/prepare-rechunk-host/action.yml`, the "If you decide to
remove this pin" section of [`docs/maintenance-watchlist.md`](../../docs/maintenance-watchlist.md),
and the commit message of #62.

**Avoid by:** reading that watchlist entry before touching the step. If you do
remove or revert it, validate on the pull request first, never by dispatching `build.yml`
on `main` — a dispatch defaults `promote_to_stable` to `true` and the first test
of the revert would then be a production promotion (#63).

---

## A new-enough `podman` on the runner is not evidence the rechunk toolchain works

**Believed:** the rechunk host only needs podman 5 or newer, so skipping the
"Update Podman" step whenever the runner already ships podman >= 5 is safe, and
it "retires automatically".

**True:** podman and crun must move together. Runner image
`ubuntu24/20260726.254` bumped podman 4.9.3 to 5.8.4 without bumping crun, so
the skip fired for the first time and run 30535305788 died in "Rechunk candidate
image with Chunkah" with `OCI runtime error: crun: unknown version specified`.
The same trap exists for buildah and skopeo. Nothing shipped: the promote job
was skipped and `:latest` did not move.

**Established by:** the before/after log comparison between runs 30479538345
and 30535305788 recorded in the commit message of #59, and the unconditional
install plus `podman --version` / `crun --version` logging and the
`podman >= 5` assertion now in `.github/actions/prepare-rechunk-host/action.yml`.

**Avoid by:** not inferring that a toolchain works from one component's version
number. When a step "retires itself" on a version check, ask what else that
step was silently supplying.

---

## Validating a revert by dispatching `build.yml` on `main`

**Believed:** after merging a change, "workflow_dispatch on main, or the Build PR
Image check" are interchangeable ways to confirm it works.

**True:** they are not. `build.yml`'s `promote_to_stable` input defaults to
`true`, so a dispatch on `main` builds, signs in the `production-signing`
environment and promotes to `:latest` in one run. Validating by dispatching
after the merge makes the production promotion path the first real test of a
change nothing has confirmed. `build-pr.yml` ("Build PR Image (No Push)")
exercises the same `prepare-rechunk-host` path without publishing anything.

**Established by:** `.github/workflows/build.yml` (`promote_to_stable`,
`default: true`) against `.github/workflows/build-pr.yml`, and the review
finding fixed in #63.

**Avoid by:** validating on the pull request first. If a post-merge dispatch is
also wanted, pass `-f promote_to_stable=false` explicitly.

---

## Passing the whole `podman inspect` to Chunkah worked until the base grew

**Believed:** exporting the entire `podman inspect` output as
`CHUNKAH_CONFIG_STR` is fine, because it had always worked.

**True:** Linux caps a single environment string at `MAX_ARG_STRLEN` (128 KiB),
and a full inspect carries `RootFS.Layers`, `History` and
`GraphDriver.Data.LowerDir`, all of which scale with the base image's layer
count. When the Aurora base went from 128 to 256 layers between
`stable-20260819.1` and `stable-20260825.1`, the payload crossed the cap and
`execve` of podman failed with `E2BIG` ("Argument list too long", exit 126) in
run 32816124214 — after the akmods cache had already built and signed. Chunkah
documents `--config-str` as taking only the `.Config` element, which stays about
2 KiB regardless of layer count.

**Established by:** the commit message of #72 (including a probe image showing
both forms yield the same resulting image config) and
`.github/actions/rechunk-native-image/action.yml`, which now runs
`podman inspect --format '{{json .Config}}'`.

**Avoid by:** passing a command only the field it documents taking. Anything
that scales with an upstream image's size will eventually cross a limit that
was never visible while it was small.

---

## The shared akmods cache probe was anonymous, and only worked because the package is public

**Believed:** `check_akmods_cache.py` can read the shared cache without
credentials, since it always has.

**True:** it worked only because this repository's cache package is public. A
fork's GHCR package is private by default, and GHCR answers an anonymous read of
a private-or-nonexistent package with a bearer-token `403`, not `manifest
unknown`. The probe then dies before the rebuild that would have seeded the
cache, so a fresh fork deadlocks its own bootstrap. `cosign verify` has the same
problem: any failure there is read as "not signed", so a correctly signed cache
would be rejected on every run and the modules rebuilt forever. `main()` now
requires `REGISTRY_ACTOR` and `REGISTRY_TOKEN` rather than falling back to
anonymous access.

**Established by:** the commit message of #64 (a live `skopeo inspect` against a
nonexistent package returned the 403) and the `require_env` calls and their
comment in `ci_tools/check_akmods_cache.py`. `build-pr.yml` still takes the
anonymous path through `prepare_validation_build.py`; #64 records that as a
decision left to the maintainer, and
`ci_tools/prepare_validation_build.py` re-raises permission-shaped failures with
the likely cause.

**Avoid by:** treating "it works" on a public package as no evidence about
authentication. An anonymous fallback is exactly what let this hide.

---

## A green akmods job does not mean a ZFS line change will work

**Believed:** if the akmods build compiles a different OpenZFS line cleanly
against the current kernel, setting `DEFAULT_ZFS_MINOR_VERSION` to it is safe.

**True:** the Aurora DX base already ships ZFS userspace, and `libzfs7`/`libzpool7`
from the 2.4 line obsolete the 2.3-era `libzfs6`/`libzpool6`. The attempt to
move to 2.3 (PR #54, closed unmerged) compiled 2.3.8 against kernel
7.0.12-201.fc44 and then failed at image build, when dnf5 refused the
transaction. The base image, not `ci/defaults.json`, constrains the ZFS line.

**Established by:** PR #54, recorded in
[`docs/safety-model.md`](../../docs/safety-model.md) and the commit message of
#55.

**Avoid by:** never citing a green akmods job as evidence for a ZFS line
change. The constraint appears at image-build time, well after the module
compiles — and a line change is also a rollback question (AGENTS.md section 0
rule 5).

---

## Checking that the CLI accepts a command by *running* it can publish

**Believed:** to verify that every `python3 -m ci_tools.cli <command>` a
workflow invokes is still a valid command, run each one with an empty
environment. They all stop at their first `require_env`, so it is a read-only
probe.

**True:** the CLI dispatches as soon as the parser accepts a name, so that runs
the handler. `akmods-build-and-publish` does not stop at a `require_env`: with
no `KERNEL_RELEASE` it falls through to `just build`, `just login`, `just push`,
`just manifest` (`ci_tools/akmods_build_and_publish.py:143-146`). Its only guard
is `AKMODS_WORKTREE.exists()`, and `AKMODS_WORKTREE` is the absolute
`/tmp/akmods`, so choosing a different working directory does not isolate it.
On a machine that had run the clone step, with whatever authentication was to
hand, a probe could publish.

**Established by:** reading `akmods_build_and_publish.py` (`main()` and the
`AKMODS_WORKTREE` constant) and the command table in `ci_tools/cli.py`.

**Avoid by:** never dispatching to check a name. An unknown command is rejected
during parsing, before any handler runs, and argparse names every accepted
choice when it does. More generally: in this repository, "it will fail fast
anyway" is a claim about a code path, and it has to be read rather than assumed.

---

## The build-inputs artifact is not named `build-inputs`

**Believed:** `gh run download <run-id> --name build-inputs` fetches the
manifest a run recorded.

**True:** the artifact is `build-inputs-${{ github.run_id }}`
(`.github/actions/prepare-main-akmods/action.yml:170`). The file inside is
`build-inputs.json`; the artifact is not.

**Established by:** reading the `upload-artifact` step rather than inferring the
name from the file it uploads.

**Avoid by:** reading the `name:` on the upload step. This one is cheap to get
wrong because the plausible name is also the filename.

---

## The `E402` per-file-ignore is inert with the pinned ruff

**Believed:** the `per-file-ignores` entry in `pyproject.toml` for
`containerfiles/zfs-akmods/install_zfs_from_akmods_cache.py` is what keeps
`ruff check` clean over its post-`sys.path` imports.

**True:** ruff 0.16.5's default rule set does not include `E402`.
`ruff check --isolated` on that file passes; `--isolated --select E402` reports
two violations. The ignore only starts mattering if the rule set is widened.

**Established by:** running both commands against the file with the ruff version
pinned in `.github/workflows/test.yml`.

**Avoid by:** not assuming ruff's defaults are `E4,E7,E9,F`. "The config must be
load-bearing or lint would fail" is not an argument — check by removing it.

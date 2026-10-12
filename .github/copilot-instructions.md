# Copilot instructions

**Read [`AGENTS.md`](../AGENTS.md) section 0 first.** It is the maintained
orientation document for this repository. This file exists so Copilot picks up
the essentials automatically; it deliberately does not restate AGENTS.md,
because a second copy of that content would drift, and documentation in this
repo has drifted from the code before.

## What this repository is

An Aurora DX bootc image with ZFS. Unlike a thin derivative, it **builds**
the ZFS kmod: a self-hosted akmods cache image is built from a pinned upstream
fork, and `containerfiles/zfs-akmods/install_zfs_from_akmods_cache.py` installs
the RPMs for one kernel out of it.

The decision-making is Python, not YAML. Every workflow step that decides
anything calls `python3 -m ci_tools.cli <command>`; the modules under
`ci_tools/` are the backbone, and `.github/scripts/README.md` maps step to
command to module.

## The one thing to internalise

**This publishes a signed `:latest` that `bootc upgrade` pulls, and there is no
staging tier between a merge and a machine booting it.** The maintainer
daily-drives this image on real hardware with multi-terabyte ZFS pools attached,
and other systems track it (see [`docs/safety-model.md`](../docs/safety-model.md)).
Merging to `main` triggers the production workflow, which builds, signs with
this repo's key, and promotes.

So the bar is higher than "tests pass":

- **A green pipeline is not a good image.** A successful run proves the build
  completed. When reporting on a run, say which run you actually read.
- **"It compiled" is not evidence a ZFS module is safe.** A module that builds
  and misbehaves sits between the user and pooled data.
- **Rollback is a data question.** An image that activates newer pool features
  can leave the previous image unable to import those pools. Never propose a
  ZFS line bump without saying that out loud.

## Never weaken a fail-closed check

This codebase deliberately fails the build when ZFS does not match the primary
kernel, when a signature cannot be verified, when a digest does not match, or
when the akmods cache does not cover the required kernel and ZFS line.

When one of those fires it is reporting a real condition. Fix the cause, or stop
and say what you found. Do not relax the check, add a fallback, make it
best-effort, or reach for `|| true` — including to make a test easier to write.
This is the single most likely wrong suggestion in this repository, because the
guard looks like an obstacle and the "helpful" fix looks like robustness.

The safety-critical files are `build.yml`,
`.github/actions/publish-native-image`, `ci_tools/sign_image.py`,
`ci_tools/promote_stable.py`, `ci_tools/check_akmods_cache.py`,
`containerfiles/zfs-akmods/install_zfs_from_akmods_cache.py`, and
`files/scripts/configure_signing_policy.py`. A change to any of them needs an
explicit statement of what could reach a booted machine if it is wrong.

## When writing code here

- Python 3, standard library only. There is no runtime dependency and no
  package to install; `ci_tools/` and `shared/` are run as modules by workflow
  steps. Do not add a dependency.
- `ruff check ci_tools/ shared/ tests/ files/ containerfiles/` must be clean,
  with the version pinned in `.github/workflows/test.yml`.
- Tests are `unittest.TestCase` throughout. `pytest` is only a runner, so
  `python3 -m unittest discover -s tests` works with nothing installed.
- Raise `CiToolError` for a known refusal. `ci_tools/cli.py` turns it into exit
  1 with the message on stderr; every workflow step runs under `set -e`.

## When writing tests

- **Never `assertRaises(CiToolError)` alone.** It passes for any reason at all,
  including an unrelated missing environment variable, so it can report a guard
  as covered when the guard never ran. Pin the message or the specific guard.
- Tests under `tests/` mock the external calls (registry, cosign, skopeo) the
  code under test makes. A green mocked test proves the logic, not the call.
- A per-module coverage floor, where one exists, may be raised freely. Lowering
  one is a decision with a reason in the commit message, never a way to make a
  change land.

## When writing comments

Comments here explain *why*, and specifically why something that looks wrong is
deliberate — the `paths-ignore` that keeps docs changes from triggering a
production build, the concurrency group that stops two `main` runs racing to
publish, the `sys.path` manipulation that makes `shared/` importable both inside
the image and from a plain script run. Several encode a specific past incident.
Do not strip or tidy them; write in the same register.

## What cannot be tested from the host

The `Containerfile` and `build_files/build-image.sh` run only inside an image
build against a real RPM database and module tree. Nothing on a developer
machine reaches them, and a green local suite is not evidence for a change to
either — say how it was verified, or say plainly that it was not.

The image-side Python is not in that category. `install_zfs_from_akmods_cache.py`
and `configure_signing_policy.py` do have host-side tests, with their external
calls mocked, and both carry coverage floors. What the suite does not prove
about them is whatever the mock stood in for.

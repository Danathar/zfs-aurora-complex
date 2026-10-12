# A revert guide that said to test in production

*2026-07-31*

## What happened

A runner image (`ubuntu24/20260726.254`) bumped podman from 4.9.3 to 5.8.4
without bumping `crun`. The rechunk step in `prepare-rechunk-host` skipped its own podman install
whenever the runner already had podman 5 or newer, "so it retires automatically",
and that skip fired for the first time and left the new podman paired with an old
`crun`. The result was `crun: unknown version specified`, in a `workflow_dispatch`
run of `build.yml` on `main`
([#59](https://github.com/Danathar/zfs-aurora-complex/pull/59)). The fix installed
the matched toolchain unconditionally and asserted podman 5 afterwards.

GitHub then rolled the runner image back
([`actions/runner-images#14473`](https://github.com/actions/runner-images/issues/14473)),
so the trigger vanished and the question became whether to remove the fix.
[#62](https://github.com/Danathar/zfs-aurora-complex/pull/62) recorded how, and in
the process wrote guidance on how to verify a revert: "merge, then confirm with
several real rechunk runs (`workflow_dispatch` on `main`, or the `Build PR Image`
check)".

An automated review posted a P1 on #62 about that sentence, about two minutes
after #62 had been merged. A `workflow_dispatch` of `build.yml` defaults
`promote_to_stable` to `true`, so it builds, signs and promotes to `:latest` in one
run. Followed literally, the guidance made the production promotion path the first
real test of an unvalidated change to the toolchain, and treated a dispatch on
`main` and a pull request check as interchangeable.

Because #62 had already merged, a commit pushed to its branch did not reach `main`.

## What changed

[#63](https://github.com/Danathar/zfs-aurora-complex/pull/63) landed the fix as
its own pull request: validate on the pull request first, through
`Build PR Image (No Push)`, which exercises the same `prepare-rechunk-host` path
without publishing, and if post-merge dispatches are wanted as well, pass
`promote_to_stable=false` explicitly. The watchlist entry kept its other warning:
one green run proves little, since run `30479538345` passed the day before run
`30535305788` failed on identical code.

## What to carry forward

**`build.yml` on `main` is not a test harness.** A dispatch defaults to promoting.
Anything that tells a reader to "run it and see" has to say which workflow, on
which ref, with which inputs, and what that run is allowed to publish.

**Documentation is a change like any other, and a revert procedure is
safety-relevant documentation.** The guidance in question sat in the entry meant to
help someone revert a safety-relevant CI fix, and it was wrong in the direction
this repository fears most. The review rubric's
[section 7](../review-rubric.md#7-documentation-that-will-drift--comment) and
[section 8](../review-rubric.md#8-automated-review-findings--answer-required)
exist because of this kind of miss.

**Check whether the pull request is still open before pushing a fix to its
branch.** A review finding on a merged pull request needs a new pull request, and
the fastest way to lose a fix is to push to a branch nothing reads any more.
This also argues for reading automated findings before merging, not after.

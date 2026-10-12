# The status language that lagged the status

*2026-08-15*

## What happened

This repository started as a testbed and became the maintainer's daily driver. The
change in status was made by configuration, not by a rewrite: signing moved into a
`main`-only `production-signing` environment
([#57](https://github.com/Danathar/zfs-aurora-complex/pull/57), merged
2026-07-29), and the docs were brought in line with it the same evening
([#58](https://github.com/Danathar/zfs-aurora-complex/pull/58)).

The framing did not follow at once. Two days earlier
[#40](https://github.com/Danathar/zfs-aurora-complex/pull/40) had already had to
"reconcile production framing across README and agent files". Then on 2026-08-15
[#70](https://github.com/Danathar/zfs-aurora-complex/pull/70) found two more
places a new reader meets early: the README intro compared this repository with
`aurora-zfs-simple` without saying which one is run, and a design document opened
by calling the repository "a controlled testbed" whose purpose was to "validate
that we can safely" do something now in production. Three pull requests over 19
days each fixed the status language that the previous one missed.

## What changed

#70 retired the testbed language, renamed `docs/zfs-aurora-testing.md` to
[`docs/design-deep-dive.md`](../design-deep-dive.md) to match what the doc map
already called it, and updated every link. It deliberately left alone the
statements that were already correct: the install warnings that assert production
status and state the limit that CI does not boot the image, since softening those
would overstate the bar rather than update the framing. It also declined to label
the sibling repository "the rollback target", which holds only while pools stay
un-upgraded, a condition a bare link list has no room to state.

## What to carry forward

**When the status of the repository changes, search for the status words, not just
the files you were editing.** "Testbed", "testing", "validate", "experiment" and
"daily-driver approach" were each load-bearing in a sentence that read as true
when it was written. A status change made by editing one document leaves a trail
of others that were correct yesterday.

**Over-claiming and under-claiming are both errors here.** Updating the framing
was not a licence to raise the bar the repository claims to have cleared. The
install warning says "safe enough for one person's own machines", and that is the
claim. Copying framing from a sibling repository that is still testing-only, without
checking it, is one way a production repository ends up described as a test one.

**A rename is a documentation change with a blast radius.** #70 renamed a file and
updated all five links, and it still recorded two pre-existing broken links it
found on the way instead of silently fixing or ignoring them.

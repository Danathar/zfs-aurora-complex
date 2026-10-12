# Reflections

Durable lessons from things that went wrong here, written down so the next
session, human or agent, starts with them instead of rediscovering them.

## How this differs from the other two

This repository already has two places that look adjacent. They answer different
questions, and keeping them apart is what stops all three becoming a diary:

| | Answers | Example |
| --- | --- | --- |
| [`.claude/memory/corrections.md`](../../.claude/memory/corrections.md) | *What did someone believe that was false?* | Deleting the rechunk host's `resolute` pin is not a safe cleanup, even though GitHub fixed the trigger |
| [`docs/upstream-change-response.md`](../upstream-change-response.md) | *Upstream did something to us. What do I do right now?* | A base image moved and the akmods cache no longer matches |
| This directory | *What did we learn about how to build and operate this repository?* | A build that succeeds proves nothing about whether the result installs |

`corrections.md` entries are **short and citable**: believed, true, established
by, avoid by. These are **retrospective and longer**: they are about a decision
made on this side, written once the dust has settled, and they usually change how
a class of work is done rather than correcting one fact.

If you are debugging a red build right now you want
[`AGENTS.md`](../../AGENTS.md) and
[`.github/prompts/diagnose-build-failure.prompt.md`](../../.github/prompts/diagnose-build-failure.prompt.md),
not this directory.

## What earns an entry

Not every fix. An entry is worth writing when the fix taught something that would
not be obvious to someone reading the resulting code:

- a failure whose cause was somewhere other than where it surfaced
- **a check that existed but proved less than it appeared to**, the most valuable
  kind here, because this repository's safety rests on checks
- a class of change this repository should treat differently from now on

A one-line fix to an obvious bug does not need an entry. A one-line fix that took
two days to find usually does.

## Format

One file per lesson, named `YYYY-MM-DD-short-slug.md`, dated when the lesson was
learned rather than when it was written up. Three headings: what happened, what
changed, what to carry forward. Link to the diagnosis (the pull request, the run)
rather than restating it.

Entries are **append-only**. If a lesson later turns out to be wrong, add a new
entry saying so and link the two. Do not quietly edit history. The point of this
directory is to be trustworthy about what was believed and when, and an edited
entry cannot be.

## Entries

| Date | Entry | Pull request |
| --- | --- | --- |
| 2026-07-28 | [The build worked and the install did not](./2026-07-28-the-build-worked-the-install-did-not.md) | [#54](https://github.com/Danathar/zfs-aurora-complex/pull/54) |
| 2026-07-31 | [A revert guide that said to test in production](./2026-07-31-a-revert-guide-that-said-to-test-in-production.md) | [#59](https://github.com/Danathar/zfs-aurora-complex/pull/59), [#62](https://github.com/Danathar/zfs-aurora-complex/pull/62), [#63](https://github.com/Danathar/zfs-aurora-complex/pull/63) |
| 2026-08-04 | [A check that never logged in](./2026-08-04-a-check-that-never-logged-in.md) | [#64](https://github.com/Danathar/zfs-aurora-complex/pull/64) |
| 2026-08-15 | [The status language that lagged the status](./2026-08-15-the-status-language-that-lagged-the-status.md) | [#40](https://github.com/Danathar/zfs-aurora-complex/pull/40), [#58](https://github.com/Danathar/zfs-aurora-complex/pull/58), [#70](https://github.com/Danathar/zfs-aurora-complex/pull/70) |
| 2026-08-25 | [The nightly build that outgrew its argument list](./2026-08-25-the-nightly-build-that-outgrew-its-argument-list.md) | [#72](https://github.com/Danathar/zfs-aurora-complex/pull/72) |

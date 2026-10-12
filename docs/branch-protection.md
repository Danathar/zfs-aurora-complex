# Branch protection

Merging to `main` publishes: `build.yml` builds, signs and promotes whatever
`main` holds to `:latest`, on every push to `main` and on a daily cron. This
file says what keeps changes to `main` behind a pull request, why each rule is
there, and how to check that GitHub is really enforcing it.

## Status

The ruleset below is active on `main` as ruleset `24901134`, created
2026-10-11 by applying [`.github/rulesets/main.json`](../.github/rulesets/main.json).
Check it yourself; neither call needs admin rights:

```bash
gh api repos/Danathar/zfs-aurora-complex/branches/main --jq .protected
gh api repos/Danathar/zfs-aurora-complex/rulesets
```

The first prints `true` and the second lists `protect main`. `false` and `[]`
mean someone has removed it, and `main` is unprotected again.

Before the ruleset, `main` had no protection at all (the calls above printed
`false` and `[]`). Direct pushes to `main` stopped on their own around
2026-07-29: every first-parent commit on `main` since 2026-07-30 is a pull
request merge. The ruleset makes that a rule rather than a habit.

## Why it matters here

Every other gate in this repository sits behind a pull request: the unit suite,
the review rubric, the risk tiers, and the rule that a pull request cannot
publish or sign. Without a ruleset nothing makes anyone open one. Any token that
holds `contents: write` could push to `main` directly, and that push would start
`build.yml`, which signs and promotes it, onto a machine someone works on.

There is deliberately no in-repo agent holding such a token
([`SECURITY-AI.md`](./SECURITY-AI.md#one-autonomous-path-and-it-is-hive)), so
the ruleset is the control against a person's mistake, a leaked token, or a
future workflow that is given write scope.

## The ruleset

[`.github/rulesets/main.json`](../.github/rulesets/main.json) is the definition
the live ruleset was applied from. It is in GitHub's import format, so it
applies as-is. What each rule does:

- **Targets `~DEFAULT_BRANCH`**, so it follows a rename of `main`. The `status`
  branch that `akmods-failure-triage.yml` pushes badges to is not covered, and
  does not need to be: nothing builds from it.
- **No bypass actors.** A bypass for Actions or for an App hands back the direct
  push this exists to stop.
- **`deletion` and `non_fast_forward`** stop `main` being deleted or rewritten.
- **`pull_request` with 0 approvals.** GitHub does not let anyone approve their
  own pull request. On a single-maintainer repository, requiring one approval
  means nothing can ever merge, including the change that relaxes the rule.
  What 0 still enforces is that every change arrives as a pull request and is
  merged through one. `dismiss_stale_reviews_on_push` is on, so a review given
  before a later push does not carry over. `require_extra_approval_for_unattributed_changes`
  is a field GitHub added to the live ruleset on its own when it was created;
  the file carries it so the file and the live ruleset agree. With 0 approvals
  required it adds no gate here.
- **One required check, `Python Unit Tests`.** It is the only check every pull
  request gets. `test.yml` runs it on every pull request, with no path filter
  and no `if:`. `Build PR Image (No Push)` in `build-pr.yml` is not required,
  because `build-pr.yml` ignores Markdown and `docs/**`, so a docs-only pull
  request never gets it and would wait forever. The `build-branch.yml` jobs
  (`Compute Branch Tag`, `Build Or Reuse Shared ZFS Akmods Cache`,
  `Build Branch Image`) run on branch pushes, not on pull requests.
  `integration_id` 15368 is GitHub Actions, so a status with the same name
  posted by anything else does not count.

The check is a unit-test and lint gate. It is not a build gate: a green
`Python Unit Tests` says the Python tools and their tests pass, not that the
image builds ([`CLAUDE.md`](../CLAUDE.md) section 0 rule 4).

Renovate does not automerge: `renovate.json` sets `automerge` to `false` in
its pin/digest rule and its OpenZFS minor-line rule, and no rule or top-level
setting turns it on (Renovate's default is off), so there is no `automergeType: branch` push for the
ruleset to break. No workflow pushes to `main`: the only `git push` in
`.github/workflows/` targets the `status` branch.

## Merge queue

GitHub's merge queue is unavailable on a repository owned by a personal account,
so the Hive criterion `acmm:merge-queue` is not credited by a file here. Hive
credits it when the repository's `merge_strategy` is `hive-serialized` and the
default branch has a known, non-empty set of required checks. The required check
is this ruleset's `Python Unit Tests`. `merge_strategy` is a setting on the Hive
dashboard that only the repository owner can change; it is not stored in this
repository, so nothing here can make it true. Until the owner sets it, that
criterion stays open.

## A pull request that never gets the check

GitHub does not start `pull_request` workflows for a pull request opened or
updated with a workflow's own `GITHUB_TOKEN`, so such a pull request would have
no `Python Unit Tests` run and could not merge. No workflow in this repository
opens pull requests with that token. If one ever does, a person closing and
reopening the pull request starts the workflows, because the `reopened` event is
then theirs. Do not fix it by adding a bypass actor.

## Applying and changing it

A pull request cannot change repository settings. A repository admin applied it
once, with:

```bash
gh api --method POST repos/Danathar/zfs-aurora-complex/rulesets \
  --input .github/rulesets/main.json
```

To change it later, edit the file through a pull request, then update the live
ruleset from the file:

```bash
gh api --method PUT repos/Danathar/zfs-aurora-complex/rulesets/24901134 \
  --input .github/rulesets/main.json
```

Renaming the `Python Unit Tests` job, adding a path filter or an `if:` to it, or
putting it in a matrix fails `tests/test_branch_protection.py`. Change the
ruleset in the same pull request, and `PUT` it straight after the merge.

## When there is a second reviewer

Set `required_approving_review_count` to 1. Consider
`require_last_push_approval`, so a push after approval needs a fresh one.

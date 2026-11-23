# Branch protection

Merging to `main` publishes: `build.yml` builds, signs and promotes whatever
`main` holds to `:latest`, on every push to `main` and on a daily cron. This
file says what keeps changes to `main` behind a pull request, why each rule is
there, and how to check that GitHub is really enforcing it.

## Status

The ruleset `protect main` is active on `main` as ruleset `24901134`, created
2026-10-11 by applying [`.github/rulesets/main.json`](../.github/rulesets/main.json).
A second ruleset, `required checks`, holds the required check on its own,
applied from [`.github/rulesets/main-checks.json`](../.github/rulesets/main-checks.json).
It was split out of `protect main` so that Renovate's bypass cannot skip the
check; see
[Renovate merges its own non-major updates](#renovate-merges-its-own-non-major-updates).
Check them yourself; neither call needs admin rights:

```bash
gh api repos/Danathar/zfs-aurora-complex/branches/main --jq .protected
gh api repos/Danathar/zfs-aurora-complex/rulesets
```

The first prints `true` and the second lists `protect main` and
`required checks`. `false` and `[]` mean someone has removed them, and `main`
is unprotected again. If the second call lists only `protect main`, the split
has not been applied yet: the check is then still inside `protect main`, which
then has no bypass either.

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

[`.github/rulesets/main.json`](../.github/rulesets/main.json) (`protect main`)
and [`.github/rulesets/main-checks.json`](../.github/rulesets/main-checks.json)
(`required checks`) are the definitions the live rulesets were applied from.
They are in GitHub's import format, so they apply as-is. GitHub enforces both
on every change to `main`. What each rule does:

- **Targets `~DEFAULT_BRANCH`**, so it follows a rename of `main`. Both files
  target it. The `status` branch that `akmods-failure-triage.yml` pushes badges
  to is not covered, and does not need to be: nothing builds from it.
- **One bypass actor, the Renovate app, and only through a pull request.**
  `protect main` lists `{"actor_id": 2740, "actor_type": "Integration",
  "bypass_mode": "pull_request"}`. 2740 is the Renovate GitHub App
  (`gh api /apps/renovate`). In `pull_request` mode Renovate can merge its own
  pull request past the rules of `protect main`. It was added so Renovate could
  merge past code owner review; that review has been off since 2026-11-23, so
  there is no owner review left for it to skip, and the bypass was left as it
  is. It still cannot push to
  `main` directly, delete it or rewrite it. A bypass only skips the rules of
  the ruleset that lists it, and `required checks` lists no bypass actors, so
  Renovate's pull request still has to pass `Python Unit Tests`. No other app,
  and not Actions, may bypass: that would hand back the direct push this
  exists to stop.
- **`deletion` and `non_fast_forward`** stop `main` being deleted or rewritten.
- **`pull_request` with 0 approvals, and no code owner review.** GitHub
  does not let anyone approve their own pull request. On a single-maintainer
  repository, requiring one approval for *every* pull request means nothing can
  ever merge, including the change that relaxes the rule. What 0 still enforces
  is that every change arrives as a pull request and is merged through one.
  `require_code_owner_review` is `false`. Owner decision 2026-11-23
  (Danathar): ordinary changes merge without the owner's review; only image
  signing, workflows, the rulesets and `CODEOWNERS` still need the owner.
  Before that it was `true`, and every Tier 3 pull request from anyone but the
  owner waited for the owner's approval on GitHub.

  **What holds the owner-merge paths now is the Hive, not GitHub.** The Hive's
  merge gate (its `auto_merge.human_merge_paths` setting, kept on the Hive and
  not in this repository) lists `cosign.pub`, `ci_tools/sign_image.py`,
  `ci_tools/promote_stable.py`, `files/scripts/configure_signing_policy.py`,
  `.github/actions/install-signing-tools/**`,
  `.github/actions/publish-native-image/**`, `.github/workflows/**`,
  `.github/rulesets/**` and `.github/CODEOWNERS`. Workflows are on the list
  because the `production-signing` environment has a branch policy but no
  required reviewer, so any workflow on `main` can ask for it and be handed the
  cosign key. That makes every workflow part of the signing path. The Hive
  labels a pull request that touches one of them `needs-owner-merge` and leaves
  it for a person to merge. [`.github/CODEOWNERS`](../.github/CODEOWNERS) lists
  the same paths, so GitHub asks the owner to review such a pull request; it no
  longer blocks the merge. Every other pull request, Tier 3 ones included
  (`.claude/`, the rest of `files/`, `ci/defaults.json`, the akmods cache
  scripts, `tests/run_tests.py`), merges on the required check and the Hive's
  AI reviewer, with no owner review. `CODEOWNERS` has no catch-all `*` line,
  and `tests/test_codeowners.py` fails if one comes back or if the list stops
  matching those nine paths.

  The Hive's gate binds what the Hive merges. GitHub does not enforce it, so a
  person, or Renovate merging its own patch, minor, digest and pin updates, is
  not held by it.

  `dismiss_stale_reviews_on_push` is on, so a review given
  before a later push does not carry over. The file also carries
  `required_reviewers: []` and `require_extra_approval_for_unattributed_changes: true`,
  both as GitHub reported them on the live ruleset, so the file and the live
  ruleset agree.
- **One required check, `Python Unit Tests`, in its own ruleset.** It is the
  only rule in `main-checks.json`, so the Renovate bypass in `main.json` does
  not reach it. It is the only check every pull request gets. `test.yml` runs it on every pull request, with no path filter
  and no `if:`. `Build PR Image (No Push)` in `build-pr.yml` is not required,
  because `build-pr.yml` ignores Markdown and `docs/**`, so a docs-only pull
  request never gets it and would wait forever.
  `Check the agent signature line` in `agent-signature.yml` runs only on Hive
  pull requests and is advisory for now; it can be added here once it has run
  clean for a month.
  `integration_id` 15368 is GitHub Actions, so a status with the same name
  posted by anything else does not count.
  Strict mode is off (`strict_required_status_checks_policy` is `false`): a
  pull request does not have to be up to date with `main` before it merges,
  but the check must pass on its own head commit. See
  [Pull requests do not have to be up to date](#pull-requests-do-not-have-to-be-up-to-date)
  and [`multi-agent.md`](./multi-agent.md#a-pull-request-does-not-have-to-be-up-to-date-before-it-merges).

The check is a unit-test and lint gate. It is not a build gate: a green
`Python Unit Tests` says the Python tools and their tests pass, not that the
image builds ([`CLAUDE.md`](../CLAUDE.md) section 0 rule 4).

Renovate merges only through a pull request (`"automergeType": "pr"`), never
by pushing its branch to `main`. No workflow pushes to `main`: the only
`git push` in `.github/workflows/` targets the `status` branch.

## Pull requests do not have to be up to date

Strict mode used to be on: GitHub refused to merge a pull request until its
branch had been updated with the latest `main` and the check had run again.
It is off now. The owner turned it off (November 2026) because of Renovate's
pull requests. Every merge to `main` made each open bump pull request stale
again, and nothing brought them up to date: `renovate.json` sets `rebaseWhen`
to `conflicted`, so Renovate does not update a branch that is only behind
`main`, and once anyone else pushes to a Renovate branch, Renovate leaves that
branch alone. So bump pull requests piled up waiting for the owner to update
them by hand, although
[AGENTS.md section 9, "Owner's standing decisions"](../AGENTS.md#9-owners-standing-decisions)
already approves routine patch and minor updates with green CI.

What still holds: `Python Unit Tests` must pass on the pull request's own head
commit before anything merges. What is given up: two pull requests that each
pass on their own can still break `main` together, because neither was tested
on top of the other. `test.yml` also runs `Python Unit Tests` on every push to
`main`, so such a pair shows red there, but only after the merge, and
`build.yml` starts on that same push.

## Renovate merges its own non-major updates

Renovate now merges its own patch, minor, digest and pin updates once the
required check passes, without the owner's approval. This is AGENTS.md's
standing decision for routine updates, applied by the bot instead of by hand.
Three settings set it up:

- `renovate.json` turns `automerge` on for those update types, with
  `"automergeType": "pr"` and `"platformAutomerge": false`. Renovate itself
  merges the pull request once its checks are green. GitHub's own auto-merge is
  not used. It was ruled out because it would wait for code owner review and
  ignore the bypass; code owner review is off now, and the setting is
  unchanged.
- `protect main` lists the Renovate app as a bypass actor in `pull_request`
  mode. It was added so its merge would not wait for code owner review on
  Tier 3 paths; that review is off now, and the bypass is unchanged.
- The required check lives in `required checks`, a ruleset with no bypass
  actors. A bypass skips only the rules of the ruleset that lists it, so CI is
  still enforced on Renovate's pull requests by GitHub, not only by Renovate's
  own wait for green checks.

What still waits for the owner: major updates, an OpenZFS minor-line bump
(new on-disk pool features can break rollback to the previous image), and a
cosign bump (`docs/architecture-overview.md` makes version-specific claims
about it). `renovate.json` keeps `automerge` off for each of those, and
`tests/test_renovate_config.py` checks that. A new GitHub Actions release also waits three days
(`minimumReleaseAge`) before Renovate takes it, so a compromised action release
has time to be found and pulled before it can merge into a signing workflow. Renovate pull requests that
change no Tier 3 path, and every pull request from anyone else, are unchanged.

`renovate.json` deliberately does not list the Hive app in
`gitIgnoredAuthors`. A commit anyone other than Renovate adds to a Renovate
branch makes Renovate stop merging that branch, and that is wanted: otherwise
Renovate would merge a Hive commit on a Renovate branch as if it were its own
update, with no review of that commit and outside the Hive's own merge gate.
When a Renovate branch needs rebuilding, the Hive ticks the rebase box on the
pull request and Renovate regenerates the branch from scratch.

## Merge queue

GitHub's merge queue is unavailable on a repository owned by a personal account,
so the Hive criterion `acmm:merge-queue` is not credited by a file here. Hive
credits it when the repository's `merge_strategy` is `hive-serialized` and the
default branch has a known, non-empty set of required checks. The required check
is `Python Unit Tests`, in the `required checks` ruleset. `merge_strategy` is a setting on the Hive
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

A pull request cannot change repository settings. A repository admin applied
`protect main` once, with:

```bash
gh api --method POST repos/Danathar/zfs-aurora-complex/rulesets \
  --input .github/rulesets/main.json
```

and creates `required checks` the same way from its own file. Create it
before the `PUT` that takes the check out of `protect main`, so that `main` is
never without a required check:

```bash
gh api --method POST repos/Danathar/zfs-aurora-complex/rulesets \
  --input .github/rulesets/main-checks.json
```

To change either later, edit its file through a pull request, then update the
live ruleset from the file:

```bash
gh api --method PUT repos/Danathar/zfs-aurora-complex/rulesets/24901134 \
  --input .github/rulesets/main.json
id=$(gh api repos/Danathar/zfs-aurora-complex/rulesets --jq '.[] | select(.name == "required checks") | .id')
gh api --method PUT repos/Danathar/zfs-aurora-complex/rulesets/$id \
  --input .github/rulesets/main-checks.json
```

Renaming the `Python Unit Tests` job, adding a path filter or an `if:` to it, or
putting it in a matrix fails `tests/test_branch_protection.py`. Change the
ruleset in the same pull request, and `PUT` it straight after the merge.

## When there is a second reviewer

Set `required_approving_review_count` to 1, and add the second reviewer to the
lines in `.github/CODEOWNERS`, so the owner's own pull requests to those paths
get a review too. Consider
`require_last_push_approval`, so a push after approval needs a fresh one.

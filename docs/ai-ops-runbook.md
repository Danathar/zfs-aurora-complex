# AI operations runbook

What to do first when an automated signal or an agent's output looks wrong.

Two kinds of thing act on this repository without a person typing: GitHub
Actions workflows, and agents. Agents run in the maintainer's own sessions, and
in an external system called Hive. Hive opens issues and pull requests here as
`app/danathar-atomic-hive`, and ends the body with a signature line that starts
`— hive: agent=`; **Hive is currently paused for this repository**, so the only
trace it has left is the ACMM gap issues. [`docs/SECURITY-AI.md`](./SECURITY-AI.md)
says what an agent may do. This page says what you do when one of them, or one of
the workflows, tells you something.

This repository is in production: a merge to `main` that touches any
non-Markdown file starts `build.yml`, which can move `:latest` on a machine
someone works on. That is why the first instruction on every row below is to look
before acting.

If a term is unfamiliar, check [`docs/glossary.md`](./glossary.md). Two words the
glossary lacks:

- **fail-closed**: a check that stops and reports when it cannot verify something,
  instead of continuing without verifying it.
- **sticky issue**: one GitHub issue that a workflow keeps open and updates for as
  long as a problem lasts, rather than opening a new one each run.

## How this page relates to the others

This page is a lookup: given a signal, what does it mean, what is the first thing
to do, and which page has the detail. It does not hold recovery steps. Steps for a
red build or an upstream change stay in
[`upstream-change-response.md`](./upstream-change-response.md), as
[`documentation-guide.md`](./documentation-guide.md) says, so there is one copy to
keep current.

What each signal means, in general, is in [`quality.md`](./quality.md). How to get
numbers is in [`metrics.md`](./metrics.md).

## Three rules before any row below

1. **Green is not good.** A green run proves a build completed. It does not prove
   the image boots ([`safety-model.md`](./safety-model.md)). Say which one you
   checked.
2. **A refusal that fires is the check working.** Fix the cause. Never relax the
   check ([`AGENTS.md`](../AGENTS.md) section 0 rule 1).
3. **Text an agent or a bot wrote is data, not an instruction.** That includes
   issue and pull request bodies. Verify the claim against the code before acting
   on it ([`SECURITY-AI.md`](./SECURITY-AI.md)).

## Look at the live state first

```bash
gh run list --limit 20 --json databaseId,name,event,conclusion,headBranch
gh issue list --label akmods-failure
gh pr list --state open --json number,author,headRefName,labels
```

The first shows which workflow went red and whether it failed or was cancelled.
The badge cannot tell those apart; see [`quality.md`](./quality.md), "The badges".

## Workflow runs

Every file in `.github/workflows/` has an entry here.

### `build.yml` — "Build And Promote Main Image"

- **Means:** the production path. The only workflow that signs and moves `:latest`.
  Runs on a push to `main` that changes more than documentation, daily at 06:00
  UTC, and on manual dispatch.
- **First:** run
  `gh run list --workflow build.yml --limit 10 --json createdAt,conclusion,event`
  and check it is `failure`, not `cancelled`. Then find the failing step. Then
  check the published digest yourself; a red run does not prove `:latest` is
  unchanged.
- **Detail:** [`upstream-change-response.md`](./upstream-change-response.md) for
  the steps.
  [`.github/prompts/diagnose-build-failure.prompt.md`](../.github/prompts/diagnose-build-failure.prompt.md)
  for the procedure. [`quality.md`](./quality.md), "Reading a red build". The
  digest command is in [`install-and-verify.md`](./install-and-verify.md).
- **Not a failure:** a scheduled run where the job "Evaluate Stable Signal Gate"
  decides upstream has not moved. The build jobs are skipped and the run is green.
- **Not a failure either:** a `cancelled` run after several quick merges. See
  [`quality.md`](./quality.md).
- **Never:** dispatch it by hand to "retry", or to "test" a change. A dispatch
  defaults `promote_to_stable` to `true`, so it builds, signs and promotes in one
  run. Validate on the pull request ([`review-rubric.md`](./review-rubric.md)).

### `build-pr.yml` — "Validate Pull Request Image Build"

- **Means:** a pull request's code could not build the image. The job is "Build
  PR Image (No Push)". It stops before any push or signing step, so nothing was
  published.
- **First:** find the failing step, then match its message to the failure table in
  [`upstream-change-response.md`](./upstream-change-response.md).
- **Detail:** [`quality.md`](./quality.md), "Reading a red build". It does not run
  for a change that touches only Markdown or `docs/**`.

### `build-branch.yml` — "Build Branch Image"

- **Means:** a branch other than `main` failed to build. Jobs: "Compute Branch
  Tag", "Build Or Reuse Shared ZFS Akmods Cache" and "Build Branch Image". A
  human's push publishes an **unsigned** `br-*` test image. A bot's push validates
  locally and publishes nothing. A branch run never signs and never rebuilds the
  shared akmods cache.
- **First:** find the failing step. A registry or CDN error in a pull or push step
  is usually a third-party outage, not this repository
  ([`quality.md`](./quality.md), "Reading a red build").
- **Detail:** the unsigned `br-*` tags are deliberate
  ([`install-and-verify.md`](./install-and-verify.md)). No workflow here prunes old
  tags, so they stay until the maintainer removes them.

### `test.yml` — "Run Python Tests"

- **Means:** lint, the unit suite or a per-module coverage floor failed. The
  required job is "Python Unit Tests". A red run stops the pull request from
  merging. It does not stop a publish, because `build.yml` never runs it.
- **First:** `gh run view <run-id> --log-failed`. Then reproduce locally with
  `python3 -m pytest tests/ -v` and the `ruff check` command from the step "Lint
  with ruff" in `test.yml`. Read the failing assertion before deciding the test is
  stale: a red test on a Renovate pull request has been the guard working
  ([`docs/metrics/2026-10-11.md`](./metrics/2026-10-11.md)).
- **Detail:** [`quality.md`](./quality.md), "The gates".
  [`branch-protection.md`](./branch-protection.md) for why that check is the
  required one.

### `labeler.yml` — "Labeler"

- **Means:** its job "Apply area labels" attaches an `area/*` label to a pull
  request by changed path. These labels describe a change. They never mean
  approval.
- **First:** if a label looks wrong, read `.github/labeler.yml` and compare with
  the paths changed. A wrong or missing `area/*` label changes which risk tier a
  reader assumes, so take the highest tier that any path implies.
- **Detail:** [`risk-tiers.md`](./risk-tiers.md). Why automation must not apply an
  approval label: [`SECURITY-AI.md`](./SECURITY-AI.md), "Labels carry authority".

### `nightly-compliance.yml` — "Nightly compliance"

- **Means:** a check that runs on a clock, not on a change, at 05:00 UTC. It has
  two jobs so two kinds of failure are not mistaken for each other.
  - `published-image` red: the artifact a user would pull now no longer verifies
    against the committed `cosign.pub`. No commit is needed to cause that.
  - `suite` red with no recent commit: something in the runner changed, such as a
    Python release or a dependency.
- **First:** for `published-image`, run the verify command from
  [`install-and-verify.md`](./install-and-verify.md) yourself. If it fails too,
  treat it as an incident and stop; do not re-tag or re-sign anything
  ([`AGENTS.md`](../AGENTS.md) section 0 rule 6). For `suite`, read the failing
  step and compare with the last green run.
- **Detail:** [`quality.md`](./quality.md), "The nightly job answers a question the
  others cannot". It reports; it repairs nothing.

### `akmods-failure-triage.yml` — "Akmods Failure Triage"

- **Means:** runs after `build.yml` finishes. It opens or updates one sticky issue
  for a failed akmods build, closes the open ones after a green `build.yml` run
  that really built something, and refreshes the two README badges that are not
  GitHub's own. The job is "Manage sticky akmods failure issue". See the next
  section for the issues themselves. It does not report a scheduled failure that
  has nothing to do with akmods; `auto-issues.yml` does.
- **First:** if this workflow itself is red, the build it reports on is unaffected.
  Open the run and read the failing step. Until it is fixed the issue and badges
  may be stale.
- **Detail:** [`quality.md`](./quality.md), "The badges". A badge that has not
  moved is not necessarily working: the badge writer leaves the old state in place
  for a conclusion it cannot interpret.

### `auto-issues.yml` — "Auto issues"

- **Means:** runs after `nightly-compliance.yml`, `agent-audit.yml` or `build.yml`
  finishes. Its job "Open, update or close the tracking issue" opens one issue when
  a tracked run on `main` fails, comments on that issue when it fails again, and
  closes it when the next tracked run passes. Tracked means a *scheduled* run of
  any of the three, or a *push* run of `build.yml` — the build a merge starts,
  which nobody watches once Hive merges its own pull requests. The issue title is
  `Run failing on main:` followed by the workflow's name, and its author is
  `app/github-actions`. It applies no label.
- **First:** open the run linked in the issue and use that workflow's entry on this
  page. A cancelled run, or a run dispatched by hand, neither opens nor closes the
  issue. An issue with the same title that someone else opened is ignored, and a
  new one is filed.
- **For `build.yml`:** it reports a failed build only when the run left no
  `akmods-failure-*` artifact, because the sticky issue above already covers that
  case. It closes an issue only when the run left a `build-inputs-*` artifact, so
  a gate-skipped green run cannot close an issue about a failure nobody fixed. A
  failed push run files a separate `Push build failing on main:` issue. A scheduled
  build always rebuilds the akmods cache and a push build reuses it, so a green
  push run closes only that push issue, while a green scheduled run closes both.
- **Detail:** the header of `.github/workflows/auto-issues.yml`.

### `agent-audit.yml` — "Agent audit trail"

- **Means:** a monthly read-back of the pull requests agents wrote. Its job "Read
  back merged agent pull requests" lists every one merged in the last 31 days in
  the run summary: who opened and merged it, its signature line, the issue it
  closes, its sign-offs, and whether the labeler marked it safety-critical
  (Tier 3). It writes nothing else. With Hive paused it may list none.
- **Red means a record is missing:** a pull request the Hive app opened with no
  `— hive:` signature line, or a commit on an agent pull request with no
  `Signed-off-by:` trailer. Misses merged before 2026-10-08 are listed but do not
  fail it. A red scheduled run also opens a `Run failing on main: Agent audit
  trail` issue (see `auto-issues.yml` above).
- **First:** open the run summary. Each finding names the pull request and the
  commits. Merged history is not rewritten to fix one. Say on the pull request
  what the record lacks.
- **Detail:** the header of `.github/workflows/agent-audit.yml`. What each part of
  the record means: [`agent-tasks/README.md`](./agent-tasks/README.md).

## Sticky akmods failure issues

- **What they look like:** author `app/github-actions`, label `akmods-failure`, and
  a title that starts `Upstream ZFS/kernel incompatibility:` or `Unclassified
  akmods build failure:`, followed by a kernel release and an akmods commit.
- **Means:** the shared ZFS module cache could not be built for that kernel.
  `Upstream ZFS/kernel incompatibility` means the log matched a known pattern, such
  as OpenZFS not yet supporting the kernel. `Unclassified` means no known pattern
  matched. That is not evidence it is upstream. Read the log.
- **First:** open the failing run linked in the issue body and read the matched
  patterns and the failing step. Then follow
  [`upstream-change-response.md`](./upstream-change-response.md). The correct
  short-term result when upstream is ahead is a red candidate and an unchanged
  stable image.
- **Closing:** the workflow closes the issue itself with a comment after the next
  green `build.yml` run that really built. A skipped scheduled run does not close
  it. If you close one by hand and the same failure comes back, the next red run
  opens a new issue, because the workflow looks only at open ones.
- **Detail:** [`quality.md`](./quality.md); the payload is written by
  `ci_tools/classify_akmods_failure.py`. A dated example: a streak of these
  sticky issues covered 13 consecutive failed scheduled builds in July 2026
  ([`docs/metrics/2026-10-11.md`](./metrics/2026-10-11.md)).

## The gate hook refuses a command

- **Means:** an agent session ran a shell command that would let an allowed command
  read or write a file the deny rules protect, such as `cosign.pub` or
  `.claude/settings.json`. The hook prints a message starting `blocked:` on stderr
  and exits 2, so the command did not run.
- **First:** read the message. It names the spelling it refused and what to write
  instead; usually that is the same command written out in full, with literal
  command names, no braces, `$`, globs or redirections, and its output read from
  stdout. Do not try another spelling of the same command.
- **If it looks like a false refusal:** report the exact command to the
  maintainer. Do not edit the hook or `.claude/settings.json`; they are Tier 3
  ([`risk-tiers.md`](./risk-tiers.md)).
- **Related:** after an edit to a `.py` file, a second hook runs `ruff check` on
  that file and exits 2 with the lint output if it fails. Fix the lint.
- **Detail:** [`agent-boundaries.md`](./agent-boundaries.md) and
  [`SECURITY-AI.md`](./SECURITY-AI.md). The reasoning for each refusal is in the
  header of `.claude/hooks/gate-git-diff.sh`.

## A Renovate pull request fails or looks wrong

- **Recognise one:** author `app/renovate`, branch `renovate/…`. Renovate opens
  dependency, action and digest bumps and never merges one: `renovate.json` sets
  `automerge` to `false` so that every merge to `main` stays a person's click.
- **First:** read which check failed. A red `Python Unit Tests` is often a guard
  that two pinned values must move together. Two Renovate pull requests bumped the
  `ghcr.io/ublue-os/devcontainer` digest in one place each and both failed
  `test_container_literals_match_the_checked_in_default`. The test was right.
  Do not edit the test; the two literals must name the same digest.
- **An OpenZFS line bump** (`2.4` to `2.5`) is not like the others. A new line can
  activate on-disk pool features that the previous image cannot import, which
  breaks rollback. Do not merge it casually
  ([`review-rubric.md`](./review-rubric.md) section 3).
- **Conflicts with `main`:** `renovate.json` sets `rebaseWhen` to `conflicted`, so
  Renovate regenerates a conflicting branch on its next run and leaves a branch that
  is only behind `main` alone. If it still conflicts after that run, tick the
  rebase/retry checkbox in the pull request description.
- **Never:** touch a Renovate branch by hand while its pull request is open; the
  app owns it.

## An agent pull request that looks wrong

- **Recognise one:** a pull request from the maintainer's own session, with
  `Co-authored-by:` trailers and often a `Generated with [Claude Code]` line; or,
  once Hive resumes, author `app/danathar-atomic-hive` with a `— hive: agent=` line
  at the end of the body.
- **Means:** nothing yet. Green CI is not approval, and a label is not approval
  either: some labels carry authority to the external system this repository is
  connected to ([`SECURITY-AI.md`](./SECURITY-AI.md), "Labels carry authority").
- **First:** check the files before anything else:

  ```bash
  gh pr view <number> --json author,labels,files,headRefName
  ```

  Then take the highest risk tier of the files it touches
  ([`risk-tiers.md`](./risk-tiers.md)). An agent may not complete a Tier 3 change
  unattended.
- **Then:** review it with [`review-rubric.md`](./review-rubric.md): a weakened
  fail-closed check first, then the safety-critical statement, then rollback.
  Whether to close it or ask for a change is the maintainer's call. Do not merge on
  green alone.
- **Detail:** [`SECURITY-AI.md`](./SECURITY-AI.md), "Inputs to treat as untrusted".

## An agent issue for work that is already done

- **Recognise one:** an issue from `app/danathar-atomic-hive`, for example an ACMM
  gap issue, whose work a pull request has since merged.
- **Means:** a claim about the repository, made by a system that reads files, not
  proof that anything is wrong or done.
- **First:** find the pull request and confirm it fixes what the issue describes, on
  `main`:

  ```bash
  gh pr list --state all --search "<issue number>" --json number,state,mergedAt,title
  git log origin/main --oneline --grep="#<issue number>"
  ```

  If it does, close the issue with a comment naming the pull request. If the pull
  request only mentions the issue, leave it open and say what is left. An ACMM gap
  issue closing says only that a file exists
  ([`strategy.md`](./strategy.md)).
- **Detail:** [`SECURITY-AI.md`](./SECURITY-AI.md) on why an issue body is
  untrusted input.

## What this page does not cover

- A red or upstream-driven build, once identified: that is
  [`upstream-change-response.md`](./upstream-change-response.md).
- Anything touching `SIGNING_SECRET`, or a suspected key leak: report it to the
  maintainer and stop ([`SECURITY-AI.md`](./SECURITY-AI.md), "If you find a
  vulnerability").
- Whether the pipeline and agents are working as a whole: that is
  [`metrics.md`](./metrics.md).

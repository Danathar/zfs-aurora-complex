# Strategy

If a term is unfamiliar, check the shared glossary first:
[`docs/glossary.md`](./glossary.md)

What this project is trying to be, and the commands that show how far along it
is. This page links to where the goal is written down and does not restate it.
It stores no counts: every number comes from a command you run, because a number
written here would be stale the next time a pull request merged.

It is not a dashboard and nothing keeps it current by itself. If a command below
prints something that disagrees with this page, the command is right.

## The goal, where the repository states it

The goal is written in four places. Read them in this order.

1. [`README.md` → Why This Repo Exists](../README.md#why-this-repo-exists) says
   what the image is for: a signed Aurora DX image whose kernel and ZFS modules
   match, so a new Fedora kernel cannot publish a mismatched image.
2. [`docs/safety-model.md`](./safety-model.md) says what the promise is **today**
   and what it is not. The maintainer's production host switched onto this image on
   2026-07-29, so it is production for one maintainer. The README's install warning
   gives the bar it has cleared: "safe enough for one person's own machines", not a
   vendor support commitment. The safety model says what is missing: CI does not
   boot the image or import a pool before `:latest` moves, and the recovery model
   is image rollback.
3. [`docs/production-boundary-proposal.md`](./production-boundary-proposal.md)
   records the controls that were put around the signing key and `main` (status
   **adopted** on 2026-07-29): a `main`-only `production-signing` environment, and
   branch protection. Its status section says what was checked live.
4. [`docs/runtime-validation-proposal.md`](./runtime-validation-proposal.md) says
   what is deliberately not tested: the maintainer **declined** a runtime test
   tier on 2026-07-28 and accepted rollback as the mitigation. It also names the
   one case rollback does not cover (a new OpenZFS *line* activating pool features)
   and what must stay deliberate because of it.

[`docs/maintenance-watchlist.md`](./maintenance-watchlist.md) records what stands
in the way of the project quietly going stale: pins and decisions that no
automation watches. The proposals are decisions with reasons, not roadmap items
with dates.

So the stated direction is: keep a signed, ZFS-matched image building every night,
keep the failure modes closed (a mismatch refuses to publish rather than
publishing), keep rollback working, and keep every change to that path a human
decision. This page does not decide anything beyond that.

## Reading progress

Each command is a read. The `grep` and `sed` commands run from the root of a clone
of this repository. The `gh` commands name the repository, so they work from any
directory and do not depend on which fork `gh` would guess. `gh` is the GitHub
command-line tool, signed in to an account that can read the repository.

### Is the nightly image still being refreshed

The point of the project is an image that tracks Fedora and upstream Aurora
without a human in the loop for the routine case. The measure is not a pass rate
but how many scheduled builds in a row were lost:

```bash
gh run list -R Danathar/zfs-aurora-complex --workflow build.yml --event schedule --limit 30 \
  --json createdAt,conclusion -q '.[] | "\(.createdAt[0:10])\t\(.conclusion)"'
```

[`docs/metrics.md`](./metrics.md) says how to read it and classify a failure, and
[`docs/quality.md`](./quality.md) says what each signal means. A scheduled run the
stable-signal gate skipped is green and built nothing; that is not a failure.

### What is still open on the watchlist

List the entries the watchlist currently holds:

```bash
sed -n '/^## Open items/,/^## Related/p' docs/maintenance-watchlist.md | grep '^### '
```

Each hit is a heading. The section under it says what to watch for and what
happens if it fires. They are the decisions that will need a human later.

### Whether runtime validation has started

The runtime-validation proposal says no workflow gates promotion on a boot or a
pool import, and that this was a decision. Look for one:

```bash
grep -rniE 'zpool (create|import)' .github/workflows
```

No output means no workflow creates or imports a pool. Output means someone
changed that decision; read the proposal's own text against it before believing
it is finished.

### What merged recently, by branch prefix

Group merged pull requests since a date by the first part of their branch name:

```bash
since=$(date -d '30 days ago' +%F)
gh pr list -R Danathar/zfs-aurora-complex --state merged --search "merged:>=$since" \
  --limit 200 --json headRefName \
  -q '[.[].headRefName | split("/")[0]] | group_by(.) | map({prefix: .[0], merged: length}) | sort_by(-.merged)[]'
```

Read it with three cautions:

- A prefix is a naming habit, not proof of who wrote the change. Use
  [`docs/metrics.md`](./metrics.md#pull-request-acceptance) to split by author.
- `--limit 200` caps the list. If the count equals the cap, narrow the date.
- This counts activity, not progress. Many merges under one prefix can mean steady
  maintenance (`renovate/` is dependency bumps) or a loop of small fixes. Open a
  few and ask whether they moved anything in the previous sections.

### What is waiting on a person

Every pull request here is merged by the maintainer, so the queue is simply the
open pull requests. Renovate's wait for the maintainer to get to them; the
OpenZFS line bump is the one that must stay a deliberate decision
([`review-rubric.md`](./review-rubric.md) section 3):

```bash
gh pr list -R Danathar/zfs-aurora-complex --state open --json number,title,author,createdAt \
  -q '.[] | "#\(.number)\t\(.createdAt[0:10])\t\(.author.login)\t\(.title)"'
```

The issues the Hive ACMM evaluation opened are the other open work:

```bash
gh issue list -R Danathar/zfs-aurora-complex --state open --search 'author:app/danathar-atomic-hive' \
  --limit 200 --json number,title -q '.[] | "#\(.number)\t\(.title)"'
```

ACMM is Hive's maturity model for how autonomously a repository can be maintained.
Its checks test that a file exists, so a gap issue closing says nothing about
whether anything changed. This page exists to be read, not to raise a score.
Hive is active for this repository, so its agents work these issues as well as
the maintainer. An agent claims an issue in a comment before it starts, so read
the comments before taking one ([`multi-agent.md`](./multi-agent.md)).

### Whether the pipeline itself is healthy

That is a different question with its own pages. Use
[`docs/metrics.md`](./metrics.md) for the commands and
[`docs/quality.md`](./quality.md) for what each signal means.

## Why there is no scheduled report

The ACMM gap issue for this criterion lists
`.github/workflows/strategy-report.yml` as one of four filenames it accepts. This
repository does not have that workflow, on purpose; this page is the filename it
uses instead:

- **The repository already says it has no collector.**
  [`docs/metrics.md`](./metrics.md) opens: "There is no metrics service and no
  scheduled collector". A scheduled progress report is a collector.
- **The honest numbers cannot carry a conclusion.** The same page lists what is
  [deliberately not measured](./metrics.md#what-is-deliberately-not-measured) and
  explains that the pull request acceptance rate mostly measures how often the
  maintainer merges their own work. A report that printed such figures on a
  schedule would invite exactly that reading.
- **It would not be a documentation change.** Every workflow must be covered by
  [`.github/policies/workflow-permissions.json`](../.github/policies/workflow-permissions.json),
  or a test fails, and [`docs/risk-tiers.md`](./risk-tiers.md) puts that file in
  Tier 3. A new scheduled workflow that wrote to issues or a branch would also be
  new automation holding a token, in a repository where a merge to `main`
  publishes a signed image.
- **The scheduled workflows that do exist** are `build.yml`,
  `nightly-compliance.yml` and `agent-audit.yml`, and `auto-issues.yml` runs after
  them. Each does one job, and none reports on progress: `agent-audit.yml` is red
  or green on whether merged agent pull requests left their signature line and
  sign-offs.

If a report is ever wanted, the commands above are the starting point, and the
change goes through [`docs/risk-tiers.md`](./risk-tiers.md) like any other
workflow. Until then the commands are the report.

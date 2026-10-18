# Metrics

Reproducible commands, and an honest account of what the numbers are worth on a
repository this size.

There is no metrics service and no scheduled collector. Adding one would be more
machinery than the signal justifies for a single-maintainer repository. What
follows is what to run when the question actually comes up, and how to avoid
drawing a conclusion the data does not support.

**Every figure quoted below is dated.** They are worked examples of reading the
output, not facts about the repository: the commands are the durable part. If a
number here disagrees with what the command prints, the command is right and the
number is stale. That is expected, and it is why each one carries a date.

Dated readings of every section below are kept in
[`docs/metrics/`](./metrics/2026-10-11.md), one file per reading, named for the
day it was taken. Each carries the exact commands that produced it, scoped to a
fixed range of pull requests and runs, and is left as it was read.

[`docs/quality.md`](./quality.md) is the companion: this page is how to get
numbers, that one is what the gates and signals mean. For where the project is
headed rather than how to get a number, see [`docs/strategy.md`](./strategy.md).

All `gh` commands below run from a clone of this repository. Outside a clone,
add `-R Danathar/zfs-aurora-complex`.

## Pull request acceptance

```bash
merged=$(gh pr list --state merged --limit 1000 --json number -q 'length')
unmerged=$(gh pr list --state closed --limit 1000 --json number,mergedAt \
  -q '[.[] | select(.mergedAt == null)] | length')
printf 'merged %s, closed unmerged %s\n' "$merged" "$unmerged"
```

That is the headline figure, and on its own it is close to meaningless. Most
pull requests here are opened by the maintainer or by Renovate, and nothing is
rejected because nothing has needed to be. A merge rate of this kind measures how
often the maintainer merges their own work. Break it out by who opened the pull
request before reading anything into it:

```bash
gh pr list --state all --limit 1000 --json number,state,mergedAt,author -q '
  map(.author.login |= (if . == "app/renovate" then "renovate"
                        elif . == "app/danathar-atomic-hive" then "hive"
                        elif startswith("app/") then . else "human" end))
  | group_by(.author.login)
  | map({author: .[0].author.login,
         merged: ([.[] | select(.mergedAt != null)] | length),
         closed_unmerged: ([.[] | select(.state == "CLOSED" and .mergedAt == null)] | length),
         open: ([.[] | select(.state == "OPEN")] | length)})[]'
```

The classes are:

| Class | Author login | Notes |
| --- | --- | --- |
| `human` | `Danathar` (and any contributor without an `app/` prefix) | Hand-written, including commits written with an agent in the maintainer's own session |
| `renovate` | `app/renovate` | Dependency and digest bumps |
| `hive` | `app/danathar-atomic-hive` | Hive's agents (see [`multi-agent.md`](./multi-agent.md)). Active since 2026-10-12. The Hive app merges these itself, so their wait time below measures Hive, not a reviewer |
| other `app/*` | for example `app/dependabot` | Printed under their own login |

The acceptance rate that does carry information is **per class**. A Renovate pull
request that is closed unmerged is a dependency the maintainer declined or that
was superseded. A closed human pull request is usually a split or a wrong
premise: read its closing comment, because this repository's closing comments
record why (see [`docs/reflections/`](./reflections/README.md)).

### How long a pull request waits

```bash
gh pr list --state merged --limit 1000 --json number,author,createdAt,mergedAt -q '
  map({a: .author.login, h: (((.mergedAt | fromdate) - (.createdAt | fromdate)) / 3600)})
  | group_by(.a)
  | map(sort_by(.h) | {author: .[0].a, merged: length,
        median_hours: (.[(length / 2 | floor)].h * 10 | round / 10),
        max_hours: (.[-1].h * 10 | round / 10)})[]'
```

`median_hours` takes the upper of the two middle values when a class has an even
count. Read it per class, never pooled: the maintainer's own pull requests merge
in well under an hour, while a Renovate pull request waits for the maintainer to
get to it, and averaging the two describes neither. This is a description of the
queue, not a target. Optimising it would push toward merging faster, which is the
opposite of what a repository publishing a signed `:latest` wants.

## Which changes get pushed back on

More useful than the acceptance rate, because it is about substance rather than
count:

```bash
for n in $(gh pr list --state all --limit 1000 --json number \
             -q '[.[].number] | sort | .[-40:] | .[]'); do
  inline=$(gh api "repos/{owner}/{repo}/pulls/$n/comments" --paginate \
    -q '[.[] | select(.user.login | contains("codex"))] | length')
  body=$(gh api "repos/{owner}/{repo}/pulls/$n/reviews" --paginate \
    -q '[.[] | select(.user.login | contains("codex")) | .body | scan("P[0-3] Badge")] | length')
  echo "#$n inline=$inline body=$body"
done
```

Most inline review comments here come from `chatgpt-codex-connector[bot]`. Its
findings carry a `P0` to `P3` badge and are *often* right, not automatically
right. The loop counts findings posted inline and findings inside a review body.
A pull request that accumulated several is worth reading afterward to ask
whether the class of mistake is one the tests, the rubric in
[`review-rubric.md`](./review-rubric.md), or a fail-closed guard should have
caught first. The count says where review found something, not whether the
finding was right.

## Scheduled build health

The number that matters is **not** the pass rate. It is *how many consecutive
scheduled builds were lost*, because each one is a skipped image refresh and
therefore missed Fedora and security updates for anything tracking `:latest`.

```bash
gh run list --workflow build.yml --event schedule --limit 30 \
  --json createdAt,conclusion -q '.[] | "\(.createdAt[0:10])\t\(.conclusion)"'
```

Raise `--limit` (the maximum is 1000) to see a streak that ended more than 30
days ago.

### Read the causes, not the ratio

A scheduled failure is not one story. A run of them is usually several, and the
fix for each belongs to a different party. Classify before reporting:

```bash
for id in $(gh run list --workflow build.yml --event schedule --limit 100 \
    --json databaseId,conclusion -q '.[] | select(.conclusion=="failure") | .databaseId'); do
  printf '%s  ' "$(gh run view "$id" --json createdAt -q '.createdAt[0:10]')"
  gh run view "$id" --log-failed 2>&1 | sed 's/\x1b\[[0-9;]*m//g' \
    | grep -oE "(SIGNING_SECRET is not configured|unexpected EOF|Cannot build against kernel version|Argument list too long|does not provide a kmod-zfs|Promoted digest mismatch|Error: building at STEP \"[A-Z]+ [^\"]{0,40}|failed to get run log: HTTP 410)" \
    | head -1
  echo
done
```

`--log-failed` labels the step `UNKNOWN STEP` here and dumps a lot of
image-pull and compiler noise, so the `grep` is required rather than cosmetic.
Three limits of this loop, each learned the hard way:

- **Its list of messages is a list of causes already seen.** A failure that
  prints nothing matches none of them. Read that log by hand and then add the
  message, instead of reporting "unknown".
- **The first match is not always the cause.** The akmods build ends with
  `Error: building at STEP "RUN --mount=type=cache,dst=/var/cache/dnf …`
  whatever went wrong inside that step. Before 2026-10-11 this page's loop had no
  pattern for the real message further up, so 14 failures classified as that
  generic line. `Cannot build against kernel version` is the specific one.
- **GitHub deletes old logs.** A run older than the retention window prints
  `failed to get run log: HTTP 410`. Those runs can be counted, not classified.

The dated worked example is in
[`docs/metrics/2026-10-11.md`](./metrics/2026-10-11.md): 21 scheduled failures
since the first on 2026-03-30, of which 14 were one cause (OpenZFS 2.4.3 refusing
a 7.1 kernel, including 13 days in a row), one was the `Argument list too long`
rechunk failure from [#72](https://github.com/Danathar/zfs-aurora-complex/pull/72),
and six predate log retention. Each failure also left `:latest` where it was,
which is the fail-closed behaviour working: the cost was a stale image, not a bad
one.

## Unit CI health

```bash
gh run list --workflow test.yml --limit 30 \
  --json conclusion -q '[.[].conclusion] | group_by(.) | map({k:.[0],n:length}) | .[]'
```

It is the cheap gate and is expected to be green. A failure here is a real signal
precisely because it is rare, and the cause is not always the pull request's own
change: the two red runs in the 2026-10-11 reading were Renovate pull requests
bumping the build-container digest in one place, which
`tests/test_workflow_build_container.py` correctly refuses until the second
place moves with it. Note that `cancelled` is common and benign: back-to-back
merges cancel each other, see [`quality.md`](./quality.md).

## CI job durations

[`.github/auto-qa-tuning.json`](../.github/auto-qa-tuning.json) records the policy
for reading job durations: the slowest of the last `sample_size` completed runs.
Sampling is left to this command on purpose. Nothing runs it on a schedule, and
nothing rewrites a timeout from its output.

Most jobs here set no `timeout-minutes` at all, which means GitHub's 360-minute
default. That is why the useful reading is the slowest observed run rather than a
comparison against a cap.

```bash
n=$(jq -r '.policy.sample_size' .github/auto-qa-tuning.json)
for wf in $(ls .github/workflows); do
  gh api "repos/{owner}/{repo}/actions/workflows/$wf/runs?status=completed&per_page=$n" \
    --jq '.workflow_runs[].id' | while read -r run; do
    gh api "repos/{owner}/{repo}/actions/runs/$run/jobs" --jq '.jobs[]
      | select(.conclusion != "skipped" and .started_at != null and .completed_at != null)
      | "\(.name)\t\((.completed_at | fromdate) - (.started_at | fromdate))"'
  done | awk -F '\t' -v wf="$wf" '$2 > max[$1] { max[$1] = $2 }
    END { for (job in max) printf "%s\t%s\t%.1f min\n", wf, job, max[job] / 60 }'
done | sort
```

It prints one line per workflow and job: the longest run of that job among the
workflow's last `sample_size` completed runs. It makes one API call per run plus
one per workflow, and takes about a minute.

- **The Jobs API names a job by its `name:`, not its key.** `build-zfs-akmods`
  prints as `Build Shared ZFS Akmods Cache`. Match them through the job's `name:`
  line in the workflow.
- **A job with no line had no started run in the sample.** Skipped jobs are
  dropped, so a workflow whose jobs are mostly skipped may print nothing.
- **A run that was cancelled mid-job counts at the length it ran.** A short
  maximum can mean a job that was killed early rather than one that is fast.

The dated reading is in [`docs/metrics/2026-10-11.md`](./metrics/2026-10-11.md):
the longest jobs are the image builds and the akmods cache build, at around
20 minutes.

## Coverage

Not a percentage. The number CI enforces is a **per-module covered-statement
floor**, and the gate prints every module's standing on each run:

```bash
python3 -m pytest tests/ \
  --cov=ci_tools --cov=shared \
  --cov=containerfiles/zfs-akmods --cov=files/scripts \
  --cov-branch --cov-report=json
python3 tests/check_coverage.py
```

`coverage: could raise <module>: floor N, reached M` means a test landed that
reaches more than the recorded floor. Raising it locks that in. See
[`CONTRIBUTING.md`](../CONTRIBUTING.md) for why lowering one is a decision
rather than a command. `.coverage-thresholds.json` also lists, with reasons, the
shipped files coverage cannot reach (the `Containerfile`,
`build_files/build-image.sh` and `.claude/hooks/gate-git-diff.sh`).

## What is deliberately not measured

- **A repository-wide coverage percentage as a gate.** It falls when code is
  added and lets one module mask another. The percentage moving is not a finding.
- **Review latency as a goal.** The wait times above are a description. At this
  volume they are noise, and driving them down would reward merging faster.
- **Whether the image boots.** No CI step boots the image or imports a pool before
  `:latest` moves ([`safety-model.md`](./safety-model.md),
  [`runtime-validation-proposal.md`](./runtime-validation-proposal.md)). A green
  build number is not evidence about that. `boot-test.yml` boots `:latest` after
  it moves and reports in its run summary; nothing here counts those runs.
- **Production execution coverage.** There is no instrumentation on the
  production workflows. Whether a path actually runs in production is established
  by reading a real run's log, by hand.

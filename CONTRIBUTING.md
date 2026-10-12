# Contributing

Read [`AGENTS.md`](./AGENTS.md) section 0 before your first change, and
[`docs/safety-model.md`](./docs/safety-model.md) for what this repository
actually guarantees today. This repository is in production: the maintainer
daily-drives the image it publishes on real hardware with real ZFS pools, and
a merge to `main` that touches any non-Markdown file starts a
[`build.yml`](./.github/workflows/build.yml) run that can move `:latest`.
AGENTS.md's caution rules apply to every change, and they override the
general guidance that follows them and they override this page too.

If a term is unfamiliar, check [`docs/glossary.md`](./docs/glossary.md) first.
If you are on the other side of a change,
[`docs/review-rubric.md`](./docs/review-rubric.md) is what a review here checks
and in what order, and [`docs/risk-tiers.md`](./docs/risk-tiers.md) says how
much scrutiny a path gets (Tier 1, 2 or 3).

## Submitting a change

This repository is licensed under the [Apache License 2.0](./LICENSE).
Submitting a contribution means it may be distributed under those terms.

1. Unit tests pass and `ruff check` is clean (see [Tests](#tests)).
2. Every changed line traces to the change you set out to make. Adjacent
   cleanups belong in their own pull request.
3. If you touched one of the safety-critical files named in `AGENTS.md`
   section 0 rule 2, say in the pull request body **what could reach a booted
   machine if the change is wrong**. That statement is the review, not a
   formality. The [pull request template](./.github/pull_request_template.md)
   has a section for it.
4. If you changed the ZFS line, the kernel it builds against, or anything
   pool-facing, address rollback explicitly: an image that activates newer
   pool features can leave the previous image unable to import those pools.

## Tests

[`docs/code-reading-guide.md`](./docs/code-reading-guide.md#running-tests) covers
the layout and the reading order. To match what
[`.github/workflows/test.yml`](./.github/workflows/test.yml) installs today:

```bash
pip install pytest pytest-cov "ruff==0.16.5" pyyaml
python3 -m pytest tests/ -v
ruff check ci_tools/ shared/ tests/ files/ containerfiles/
```

`ruff` is pinned because an unpinned local copy can enable or disable different
rules release to release and disagree with CI in either direction; a Renovate
custom manager in [`renovate.json`](./renovate.json) tracks that pin. Its
settings (a 100-column line length and one per-file `E402` exemption) live in
[`pyproject.toml`](./pyproject.toml), and
[`.editorconfig`](./.editorconfig) follows them for editors. `pyyaml` is what
the issue-form tests parse YAML with; they import it behind `except ImportError`
and skip without it, so leaving it out does not fail the run — it quietly runs
fewer tests than CI does. `pytest` is only a runner here — the suite is
`unittest.TestCase` throughout, so `python3 -m unittest discover -s tests`
also works with nothing installed at all, which is useful when you have no
network.

Add `pytest-cov` for the [Coverage](#coverage) section below. `test.yml`
installs it too, so the coverage numbers and the per-module gate you see
locally are the same ones CI produces on every pull request.

`tests/e2e/` is collected by that same command and needs nothing extra. It runs
the CLI as a real subprocess rather than importing it — see
[`tests/e2e/README.md`](./tests/e2e/README.md) — and moves no coverage
number, because the code it exercises runs in a child process.

## Coverage

CI reports coverage on every pull request and push to `main`. Reproduce the
same number locally with the command that workflow step runs:

```bash
python3 -m pytest tests/ \
  --cov=ci_tools --cov=shared \
  --cov=containerfiles/zfs-akmods --cov=files/scripts \
  --cov-branch --cov-report=term-missing
```

**The percentage is reported, not gated.** There is no `--cov-fail-under`, and
no pull request is blocked on the percentage. The number exists so a reviewer
can read it off the job log instead of reconstructing it by hand.

### What *is* gated: per-module floors

[`.coverage-thresholds.json`](./.coverage-thresholds.json) records, for each
measured module, the number of statements the suite reaches today.
[`tests/check_coverage.py`](./tests/check_coverage.py) enforces those floors in
`test.yml`, and its module docstring carries the reasoning at length. In short:

- **Counts, not percent.** A percentage falls when code is added, so it
  punishes growth. A covered-statement count falls only when a test stops
  reaching lines that used to run.
- **Per module, not repository-wide.** One well-tested module cannot mask
  another that stopped being exercised at all.
- **Both directions.** A measured module with no floor fails, so new code
  arrives with a recorded decision. A floor naming a module the run did not
  measure also fails, so the manifest cannot rot after a rename.
- **Every shipped file, not only the measured ones.** The floors can only cover
  what `--cov` measures, which left a hole: a file that ships and executes but
  sits outside those paths had no entry anywhere. So the manifest has a second
  section, `unmeasured`, and every tracked `*.py`, `*.sh` and `Containerfile`
  outside `tests/` must appear in one section or the other. An entry there
  needs a **reason**, not just a listing — an empty reason is rejected, because
  it records no decision and only silences the check.

Today `unmeasured` holds `Containerfile` and `build_files/build-image.sh`, which
run only inside an image build, so nothing on the host reaches them, and
`.claude/hooks/gate-git-diff.sh`, a bash hook coverage.py cannot instrument that
`tests/test_git_diff_gate.py` runs as a subprocess. If you add
a shipped file and nothing else, the gate goes red. That is the point — the
choice gets made once, in the open, rather than drifting.

Raising a floor is evidenced -- the suite demonstrably reached those lines, and
the gate prints `could raise` when it does. Lowering one is evidenced by an
absence, and a deleted test and a deliberately removed code path look identical
from the count alone. So a lowered floor is a line in a diff with a reason in
the commit message, never something applied automatically.

This is not a coverage percentage in disguise. If your change legitimately
removes a code path, the floor moves down with it and you say so.

To run the gate the way `test.yml` does, add `--cov-report=json` to the command
above and then:

```bash
python3 tests/check_coverage.py
```

### Three tiers, and they answer different questions

This repository has one *measured* tier, one *unmeasured but unmocked* tier,
and one *real but unmeasured* tier. They are not interchangeable, and no one of
them alone means "tested".

| | What it measures | Where it runs | Instrumented? |
|---|---|---|---|
| **Unit coverage** | Decision logic, with **every** external call mocked | `test.yml`, on every PR and push | Yes |
| **End-to-end (`tests/e2e/`)** | The process boundary a workflow step depends on: exit status, and the `GITHUB_OUTPUT` file a later step reads | `test.yml`, on every PR and push | **No** — the code under test runs in a child process |
| **Production execution** | The same modules against a real registry, real `cosign`, real `git` remote, real `podman` | `build.yml` (daily at 06:00 UTC and on pushes to `main`), `build-pr.yml`, `build-branch.yml`, `akmods-failure-triage.yml` | **No** |

The first row is the important caveat. As
[`docs/code-reading-guide.md`](./docs/code-reading-guide.md#running-tests)
states, all subprocess, registry, and filesystem calls are mocked there so the
suite runs without network access or container tooling. That is the right
design for a fast suite, but it means **a module at 100% unit coverage may
never have had its real-world path exercised**, and a module in the 60s may be
running against a live registry every night.

The second row exists because of a specific blind spot in the first, and
[`tests/e2e/README.md`](./tests/e2e/README.md) states it: an in-process test
calls `main()`, so it never observes the exit status. A guard that reports a
problem on stderr and then exits `0` does not stop a workflow step running
under `set -e`, and the unit suite passes on it either way. That tier reaches
no registry, `cosign`, `podman`, or `git`, so it is not a substitute for the
third row.

So the tiers are close to orthogonal. Read them together or you will draw the
wrong conclusion from any one of them.

### Establishing whether a path runs in production

There is no coverage instrumentation on the production workflows, so today the
only way to answer this is by hand — `gh run list` for the trigger mix, then
the job logs for the branch you care about. A path reachable only from a manual
`workflow_dispatch` (for example `use_input_lock=true`, the input-lock replay
mode of `build.yml`) runs in production only when someone starts it, and the
`schedule` and `push` runs never reach it.

This does not scale as a review practice. If you find yourself doing this
archaeology repeatedly for the same file, say so in an issue.

### Priority order for a real gap

Rank a coverage gap by how many tiers miss it, not by the percentage:

1. **Covered by neither.** The highest priority: no unit test and no
   production execution. A guard on a path reachable only from a manual
   dispatch, such as an incident-replay mode, is the typical case — and it is
   the worst one, because a broken guard would surface only when someone needed
   it to diagnose an outage.
2. **Runs in production, no unit test.** Real but unverified: you find out it
   broke by watching a build fail. Worth a test, lower urgency.
3. **Unit-tested only, where a real call would behave differently.** Mocks
   agree with whatever you told them. Registry error shapes, `cosign` output
   formats, and `skopeo` failure messages are the usual offenders.

### What not to file

- **The percentage itself.** A number moving from 78% to 76% is not a finding.
  Name the path and which tier misses it.
- **Coverage of a fail-closed guard obtained by weakening it.** `AGENTS.md`
  section 0 rule 1 is absolute: when a check fires because ZFS does not match
  the primary kernel, a signature will not verify, a digest does not match, or
  the akmods cache does not cover the required kernel, the fix is the
  underlying cause. Never relax the check, add a fallback, or make it
  best-effort — including to make a test easier to write.
- **A test that asserts only that something raises.** Prefer pinning the
  message or the specific guard. `assertRaises(CiToolError)` passes for any
  reason at all, including an unrelated missing environment variable, so it can
  report a guard as covered when the guard never ran.

### A note on mocked error paths

Because every external call is mocked, error-handling branches are the ones
most likely to be covered on paper and untested in fact. When a branch exists
to handle a *real* failure — a registry timeout, a rate limit, a malformed
manifest — prefer a test that produces the real failure shape over one that
patches the call to raise.

## Documentation

[`docs/documentation-guide.md`](./docs/documentation-guide.md) is the map, and
its "Where To Put New Documentation" section says which file new prose belongs
in. Add new documents to that tree and to the router table in
[`README.md`](./README.md) in the same change.

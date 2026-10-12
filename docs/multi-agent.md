# Multiple agents in this repository

If a term is unfamiliar, check the shared glossary first:
[`docs/glossary.md`](./glossary.md)

## Purpose

More than one AI agent can open a pull request here. This page says which ones
appear in the repository's own history, where they run, how work reaches them,
how they are kept from colliding, and who merges. It describes what already
happens. It adds no automation.

It does not cover what an agent is *allowed* to do: that is
[`SECURITY-AI.md`](./SECURITY-AI.md). It does not cover what to do when an
agent's output is wrong ([`ai-ops-runbook.md`](./ai-ops-runbook.md)), or how to
trace a change back to its task ([`agent-tasks/README.md`](./agent-tasks/README.md)).

A few words used below:

- **Agent**: an AI coding session that reads the repository and writes a change.
- **Hive**: an external system, outside this repository, that starts agents and
  files issues for them. Its GitHub App appears as `app/danathar-atomic-hive`.
  Its code and settings are not in this repository, so this page can only
  describe what it leaves behind here.
- **Required check**: a check that must pass before GitHub lets a pull request
  merge.

## This repository is in production

A merge to `main` that touches any file other than Markdown starts `build.yml`,
which builds, signs and can move `:latest` on a machine that someone works on
([`AGENTS.md`](../AGENTS.md) section 0). Everything below is shaped by that. More
agents does not mean more automation here, and the repository has chosen to keep
the number of autonomous paths to one.

## There is no orchestrator in this repository

Nothing in this repository decides which agent runs next. The ACMM (an external
maturity checklist that Hive runs against the repository) looks for files named
like an in-repo orchestrator. None of these exist here:

```text
.github/workflows/dispatcher.yml
.github/workflows/orchestrate.yml
scripts/orchestrate.mjs
.claude/dispatcher/
orchestrator/
```

That is deliberate, not an omission. A workflow that starts agents on its own
schedule would be an automated way to open pull requests against a repository
where [a merge to `main` publishes a signed image](./SECURITY-AI.md). There is no
in-repo agent entry point at all: no workflow here runs an AI model, and none
holds a token that could. Hive is the one autonomous path, and it is outside the
repository. A file added only to satisfy the checklist would raise the score
without changing anything, which is the thing to avoid.

Check it yourself:

```bash
ls .github/workflows
grep -rliE 'anthropic|openai|claude-code|codex' .github/workflows
```

The first lists the workflows; none starts an agent. The second prints nothing
(it runs only if a workflow names a model provider or an agent CLI).

## Which agents appear

There are two kinds, and they leave different traces.

**Agents in the maintainer's own sessions.** The maintainer works with AI coding
tools and pushes the result under their own login. The repository carries
instructions for several of them, all pointing at one source,
[`AGENTS.md`](../AGENTS.md):

| Tool | Reads | Notes |
| --- | --- | --- |
| Claude Code | [`CLAUDE.md`](../CLAUDE.md), `.claude/` | The permission boundary lives in `.claude/settings.json` and `.claude/hooks/` ([`agent-boundaries.md`](./agent-boundaries.md)) |
| GPT-family agents, Codex | [`AGENTS.md`](../AGENTS.md) | Section 0 applies to every agent |
| Gemini | [`GEMINI.md`](../GEMINI.md) | |
| Cursor | `.cursor/rules/` | A pointer at `AGENTS.md` |
| GitHub Copilot | `.github/copilot-instructions.md` | A pointer at `AGENTS.md` |

These sessions leave a `Co-authored-by:` trailer on commits and often a
`Generated with [Claude Code]` line in the pull request body, and the pull
request's author is the maintainer. To see how many:

```bash
git log origin/main --format=%B | grep -i '^co-authored-by' | sed 's/ *<.*//' | tr 'A-Z' 'a-z' | sort | uniq -c | sort -rn
gh pr list --state all --limit 1000 --json body -q '[.[] | select(.body | test("Generated with \\[Claude Code\\]"))] | length'
```

As of 2026-10-11, 36 of the 148 commits on `main` carried a `Co-authored-by:`
trailer naming a Claude model (49 trailers in all; some commits carry more than one), and 15 of the 67 pull
requests up to #78 said "Generated with Claude Code". Both are lower bounds, because nothing requires the trailer. The model
names in the trailers change over time, so read them as a record of what ran, not
a list of what is supported.

**Review agents.** `chatgpt-codex-connector[bot]` reviews pull requests and posts
findings with a severity badge. It opens nothing and merges nothing.
[`review-rubric.md`](./review-rubric.md) section 8 says what to do with a finding.

**Hive's agents.** Hive's pull requests and issues end with a signature line of
the form `— hive: agent=<role> backend=... model=...`, and its pull requests come
from `app/danathar-atomic-hive`. **Hive is currently paused for this
repository**, so no such pull request exists yet: of the 67 pull requests up to
#78, none was opened by that app or carries that signature. The one trace Hive
has left here is the set of ACMM gap issues (numbers #79 to #121), whose last line
reads `— hive: agent=dashboard` and which come from a Hive evaluation, not from
an agent role. This page does not describe Hive's roles; read the signature on a
real pull request when one appears. [`agent-tasks/README.md`](./agent-tasks/README.md)
has the commands.

To see the current picture instead of trusting this page:

```bash
# Who opens pull requests, and from which branch prefix:
gh pr list --state all --limit 1000 --json author,headRefName \
  -q '.[] | [.author.login, (.headRefName | split("/")[0])] | @tsv' | sort | uniq -c | sort -rn

# Which pull requests carry a Hive signature:
gh pr list --state all --limit 1000 --json number,body \
  -q '.[] | select(.body | test("— hive: agent=")) | .number'
```

Both need `-R Danathar/zfs-aurora-complex` outside a checkout. A branch prefix is
the author's habit and nothing enforces it; `renovate/` and `dependabot/` are
those apps' own.

## How work reaches an agent

There are two routes. Only the first exists today, and neither lives in a
workflow.

**Route 1: the maintainer starts a session.** The maintainer opens an agent on
a clone (or a worktree) of the repository and hands it a task. The record is the
pull request the session opens, and the `Closes #N` line in its body if it has
one. Nothing in the repository receives the request.

**Route 2: Hive, outside the repository.** Hive looks at the repository and at
its own backlog, starts an agent, and the agent pushes a branch. Nothing here
receives that request, so nothing here can be asked about it. What would be
visible here is the result: a branch, a pull request, and labels. While Hive is
paused, no work arrives this way, and a pull request for an ACMM gap issue is a
person's (or the maintainer's agent's) work like any other.

There is no label that hands work to an agent here. No workflow reacts to a
label, and a label on an issue is a description, not a request.

### Labels

Labels come from three places:

- `.github/workflows/labeler.yml` applies descriptive `area/*` labels from the
  paths a pull request changes, using
  [`.github/labeler.yml`](../.github/labeler.yml). They say where a change lands,
  and by extension its tier ([`risk-tiers.md`](./risk-tiers.md)).
- Dependabot applied `dependencies` and `github_actions` to its three pull
  requests. Renovate's carry no label.
- A maintainer applies whatever else is needed.

Some labels mean more than their names suggest to the external system this
repository is connected to. [`SECURITY-AI.md`](./SECURITY-AI.md) has the reasoning
and the rule that no automation here may apply a label that reads as an approval.
This command lists the labels whose description says "auto-merge":

```bash
gh label list --limit 100 --json name,description \
  -q '.[] | select(.description | test("auto-merge"; "i")) | .name'
```

It printed nothing on 2026-10-11: no such label exists in this repository yet.
That is a property of the repository's labels today, not a guarantee. Run the
command again before relying on it.

## Keeping agents from colliding

Several agents, and the maintainer, can work at once. The repository gives only
narrow guards, and none is aimed at agents in particular.

**A pull request's own checks are cancelled when it is updated.** `test.yml`,
`build-pr.yml` and `build-branch.yml` cancel an older run for the same ref. That
keeps one pull request tidy; it does not coordinate two.

**Two `main` runs never race to publish.** `build.yml`'s concurrency group is the
workflow and the ref, with `cancel-in-progress: true`, so a newer run on `main`
cancels an older one before it can publish something based on older input. The
cost is that merging quickly turns the build badge red ([`quality.md`](./quality.md)).

Everything else is a habit, and it is a habit that the agent or the person doing
the work has to keep:

1. **Look for open work on the same files before starting.**

   ```bash
   gh pr list --state open --json number,title,headRefName,author
   gh pr list --state open --search "<file or topic>" --json number,title
   ```

2. **Look at the issue, not only the pull requests.** The ACMM gap issues are
   worked in slices. Read the open pull requests for one before taking it.
3. **Say what you took.** Put `Closes #<issue>` in the pull request body so the
   issue links to it.
4. **Keep a pull request to one change.** A small pull request that touches few
   files has few ways to overlap with another.
5. **Do not touch an open Renovate pull request.** It is the dependency app's, and
   it is rebased by the app.

### Two green pull requests can be red together

The required check is `Python Unit Tests`. The ruleset in
[`.github/rulesets/main.json`](../.github/rulesets/main.json) sets
`strict_required_status_checks_policy` to `false`. In plain terms: GitHub does not
make a pull request catch up with `main` before it merges. A pull request is
judged by the code it was tested with, not by what `main` has become.

So if pull request A and pull request B each pass, and both change the same
function from different directions, merging A and then B can leave `main` broken
even though neither ever showed red. The failure then shows on `main`, and a push
to `main` that changes more than documentation starts `build.yml`, the workflow
that signs and promotes an image. See [`branch-protection.md`](./branch-protection.md)
for the ruleset itself, and [`safety-model.md`](./safety-model.md) for what a bad
`main` can reach.

GitHub's merge queue is unavailable on a repository owned by a personal account,
so nothing serialises merges. The protection is a step the merger takes: when two
open pull requests touch the same files, merge one, update the other branch from
`main`, wait for `Python Unit Tests` to rerun, and only then merge it.

To check what the live ruleset says rather than trust this page:

```bash
gh api repos/Danathar/zfs-aurora-complex/rulesets
```

## Who merges

The maintainer, `Danathar`, merges. All 60 merged pull requests up to #78 were
merged by that login, including every Renovate and Dependabot one, and the
repository has no automerge: `renovate.json` sets `automerge` to `false`
([`branch-protection.md`](./branch-protection.md)). Check:

```bash
gh pr list --state merged --limit 1000 --json mergedBy \
  -q 'map(.mergedBy.login) | unique'
```

A merge to `main` builds, signs and promotes the image, so the required checks are
the gate and the review is the maintainer's. The ruleset requires a pull request
and a passing `Python Unit Tests`; it requires no approving review, and it allows
no bypass actors.

Agent sessions working in this repository do not merge on their own initiative.
[`AGENTS.md`](../AGENTS.md) section 0 rule 6 says not to push, promote, tag, or
delete published artifacts on your own initiative, and
[`SECURITY-AI.md`](./SECURITY-AI.md) lists what no agent may do on any instruction.
The evidence for any one pull request is a `merged` event whose actor is a person
or an app:

```bash
gh api repos/Danathar/zfs-aurora-complex/issues/<number>/events \
  --jq '.[] | select(.event == "merged") | .actor.login'
```

If Hive resumes and is given merge rights, that is a change to who may publish an
image, and it belongs in [`SECURITY-AI.md`](./SECURITY-AI.md) and
[`risk-tiers.md`](./risk-tiers.md) before it happens, not on this page after.

What to read next:

- [`SECURITY-AI.md`](./SECURITY-AI.md): what an agent may and may not do.
- [`risk-tiers.md`](./risk-tiers.md): how much review a given change needs.
- [`review-rubric.md`](./review-rubric.md): what the merger checks.
- [`branch-protection.md`](./branch-protection.md): the ruleset on `main`.

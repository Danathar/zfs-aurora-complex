# Agent Tasks: From A Change Back To Its Task

If a term is unfamiliar, check the shared glossary first:
[`docs/glossary.md`](../glossary.md)

This page answers one question: **an agent changed something here; which task was
it working on, and who asked?**

Every answer is already in GitHub and in git. This page says where to look and
gives the commands. It does not add a log.

## There Is No Task Log, On Purpose

This repository keeps no per-task record of its own. There is no `.agent/tasks/`
directory and no `.github/agent-log/` directory, and this directory holds only this
page.

The trail already exists in places that cannot drift from what happened: the issue,
the pull request, the commit, and the commit that landed on `main`. A log kept by
hand next to them would be a second copy. Someone would forget to update it, and
from then on it would be wrong. That is the same reason
[`docs/documentation-guide.md`](../documentation-guide.md) prefers a link to a copy.

## What Links A Change To Its Task

Follow these from the code outward. Each one points at the next.

| Link | Where it lives | What it tells you |
| --- | --- | --- |
| Commit on `main` | `git log` on `main` | Which pull request put the change on `main` |
| Pull request | GitHub | What was done and why, and the issue it closes if it says so |
| `Closes #N` | The pull request body | The task: the issue that asked for the work |
| Issue | GitHub | Who filed the task, and what they asked for |

Be realistic about the third row. Many pull requests here have no `Closes #N` line:
the pull request body is the task. `gh pr view 72 --json closingIssuesReferences`
prints an empty list for one that fixed a failed nightly run, because that run, not
an issue, was what asked. Where there is no such line, the body usually links the
run or the pull request it came from.

### The pull request number in the commit subject

Most pull requests land as a squash or rebase, so the commit subject on `main` ends
with the pull request number, for example `Pass only .Config to Chunkah so rechunk
stops hitting E2BIG (#72)`. A merge commit reads `Merge pull request #N from
Danathar/<branch>` instead. Both name the pull request. This is how things are done,
not a setting: the ruleset allows `merge`, `squash` and `rebase` (see
[`.github/rulesets/main.json`](../../.github/rulesets/main.json) and
[`docs/branch-protection.md`](../branch-protection.md)).

### The agent trailer

A session the maintainer runs with an agent pushes under the maintainer's own
login, so the pull request author cannot say an agent was involved. The commit
carries the evidence instead, when the tool wrote it:

```text
Co-authored-by: Claude Opus 5 <noreply@anthropic.com>
```

and the pull request body often ends with `🤖 Generated with [Claude Code]`. Both
are written by the tool, not required by the repository, so their absence proves
nothing. `git` shows the trailer without GitHub:

```sh
git log -1 --format='%an <%ae>%n%b' <sha>
```

### The Hive signature line

Hive is an outside system, not part of this repository. It opens pull requests and
issues as `app/danathar-atomic-hive` and writes a signature as the last line:

```text
— hive: agent=<role> backend=<tool> model=<model>
```

The one instance that exists in this repository today is the shorter line on the
ACMM gap issues Hive's dashboard opened (for example #91, #92 and #93):

```text
— hive: agent=dashboard
```

Hive is paused for this repository, and no pull request here has been opened by it
or carries a `— hive:` line, so there is no agent role to look up. What each role
does is Hive's to say. When a Hive pull request appears, `agent` names the role that
wrote it and its branch prefix usually matches.

The signature is **a hint, not proof**. Anyone can type that line into a comment.
Check the author, not the text. `.github/workflows/agent-audit.yml` checks the
second of these on merged pull requests: that a Hive pull request has the line and
that commits on agent pull requests carry a `Signed-off-by:` trailer.

### The branch prefix

A branch prefix is a habit, not a rule. Nothing in the repository enforces it. The
prefixes in this repository's history, read from its pull requests, are the
maintainer's own (`docs/`, `fix/`, `harden/`, `feat/`, `refactor/`, `acmm/`) and the
apps' (`renovate/`, `dependabot/`). Read the prefix together with the pull request
author. The command to see the real pairs today is in the next section.

## Commands

These need the [GitHub CLI](https://cli.github.com/) (`gh`), signed in. Replace
`<sha>`, `<N>` and the role. Run them from a clone of this repository, or add
`-R Danathar/zfs-aurora-complex`.

Find the pull request that put a commit on `main`:

```sh
gh pr list --state merged --search <sha> --json number,title,headRefName,author
```

Without `gh`, use git. A squashed commit's subject already ends in `(#N)`. For a
commit that came in on a merge, `--ancestry-path` lists the merge commits between
it and `main`. A branch that merged `main` into itself before it landed has those
merges on the path too, and they come first, so keep only the first pull request
merge:

```sh
git log --merges --ancestry-path --reverse --format='%h %s' <sha>..main | grep -m1 ' Merge pull request #'
```

Find the issue a pull request closes, if it names one:

```sh
gh pr view <N> --json closingIssuesReferences --jq '.closingIssuesReferences[].number'
```

Read the last line of a pull request or issue body, and the labels:

```sh
gh pr view <N> --json body,labels --jq '(.body | split("\n") | map(select(length > 0)) | last), [.labels[].name]'
gh issue view <N> --json body --jq '.body | split("\n") | map(select(length > 0)) | last'
```

List the pull requests that carry a Hive signature, if any:

```sh
gh pr list --state all --limit 1000 --json number,body \
  --jq '.[] | select(.body | test("— hive: agent=")) | .number'
```

See which branch prefix goes with which author, from the real pull requests:

```sh
gh pr list --state all --limit 1000 --json author,headRefName \
  --jq '.[] | [.author.login, (.headRefName | split("/")[0])] | @tsv' | sort | uniq -c | sort -rn
```

Find commits an agent co-authored:

```sh
git log origin/main --format='%h %s' -i --grep='^co-authored-by: claude'
```

Find who merged a pull request:

```sh
gh api repos/Danathar/zfs-aurora-complex/issues/<N>/events \
  --jq '.[] | select(.event == "merged") | .actor.login'
```

## What `.claude/session-summary.md` Is

[`.claude/session-summary.md`](../../.claude/session-summary.md) is **not** a task
record. It holds state carried between agent sessions: work in flight and decisions
that are not yet visible in a diff. Its own header says it is not a changelog and
that `git log` is a better one.

It has no task numbers, no run IDs and no per-change history. Do not look there to
find out why a change was made. Use the links above. It is the right place only to
ask what the next session was told to pick up.

## Where This Stops

- A change a person made in their own session with an agent may carry no trailer
  and no signature. Its trace is then the pull request that person opened, and the
  body is the task.
- This page cannot tell you why an agent chose its approach. Read the pull request
  body and the diff for that.
- This page only finds the task. It does not say who the agents are
  ([`multi-agent.md`](../multi-agent.md)), or what to do when one gets it wrong
  ([`ai-ops-runbook.md`](../ai-ops-runbook.md)).

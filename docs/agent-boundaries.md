# Agent boundaries

Which limits on an AI agent working here are enforced by a tool, and which are
only asked of it.

If a term is unfamiliar, check the shared glossary first:
[`docs/glossary.md`](./glossary.md)

## Purpose

Most of what this repository tells an agent is a request: keep a change to one
thing, write the safety-critical statement, never weaken a fail-closed check. A
**structural gate** is different. It is a setting, a check or a refusal that
stops the action whatever the agent decides, so a mistaken or misled agent is
stopped rather than trusted.

This page lists the gates, says what each one stops, and links the page that
explains it. It restates none of them; the linked page is the reference.

What an agent is *allowed* to do is [`SECURITY-AI.md`](./SECURITY-AI.md). How
much review a change needs is [`risk-tiers.md`](./risk-tiers.md).

## The gates

| Gate | What it stops | Holds for | Explained in |
| --- | --- | --- | --- |
| [`.github/rulesets/main.json`](../.github/rulesets/main.json) | Any change reaching `main` except as a pull request merge. Deleting or rewriting `main`. Its one bypass actor, the Renovate app, may only merge a pull request past its rules; nothing may bypass the required check in [`main-checks.json`](../.github/rulesets/main-checks.json). | every agent and every person | [branch-protection.md](./branch-protection.md#the-ruleset) |
| The Hive's `human_merge_paths` setting, mirrored by [`.github/CODEOWNERS`](../.github/CODEOWNERS) | The Hive merging a pull request that touches image signing, a workflow, the rulesets or `CODEOWNERS`: it labels it `needs-owner-merge` and leaves it for a person. | the Hive only: GitHub does not enforce it, since the ruleset's `require_code_owner_review` is `false`, so a person or Renovate merging is not held | [branch-protection.md](./branch-protection.md#the-ruleset) |
| The required `Python Unit Tests` check in [`test.yml`](../.github/workflows/test.yml) | Merging a pull request whose unit suite or `ruff check` fails. | every agent and every person | [branch-protection.md](./branch-protection.md#the-ruleset) |
| [`.github/policies/workflow-permissions.json`](../.github/policies/workflow-permissions.json) | A workflow's `GITHUB_TOKEN` gaining a scope the policy file does not grant: the unit suite fails until the same pull request changes both. | every agent | [SECURITY-AI.md](./SECURITY-AI.md#what-an-agent-may-do-unattended) |
| [`.claude/settings.json`](../.claude/settings.json) | Signing, registry pushes and deletes, merging, dispatching a workflow, releases, secrets, labels, the force-push and delete spellings a prefix rule can see, `git reset --hard`, `git clean`, destructive `zpool` and `zfs` commands, and reading `cosign.key`, `cosign.key.*`, `.env`, `.pem`/`.p12` files or SSH keys named `id_rsa`/`id_ed25519` (a key under another name is not denied). It asks before an ordinary push, commit or pull request. | Claude Code sessions only | [SECURITY-AI.md](./SECURITY-AI.md#the-agent-permission-boundary) |
| [`.claude/hooks/gate-git-diff.sh`](../.claude/hooks/gate-git-diff.sh) | An allow-listed command, such as `git diff` or `gh pr view --jq`, spelled so it reads or writes a file the deny rules protect, or prints the environment, whether it stands alone, behind another program, or in a string handed to a shell. | Claude Code sessions only | [SECURITY-AI.md](./SECURITY-AI.md#the-agent-permission-boundary) |
| [`tests/run_tests.py`](../tests/run_tests.py) | The one test command that runs with no prompt importing code outside `tests/` or code git does not track, or writing to a path it names. | Claude Code sessions only | [SECURITY-AI.md](./SECURITY-AI.md#the-agent-permission-boundary) |

"Claude Code sessions only" matters. The settings file and its hook are Claude
Code's format, and an agent on another backend does not read them. The runner
is a gate only because the settings file allows it and not `pytest` itself.
The gates that hold for every agent are the ones on GitHub's side.

The first four rows stop a merge, not a publish. The unit suite runs inside
`Python Unit Tests`, and `build.yml` does not run it, so the nightly build signs
and promotes whatever is on `main`. What stops a bad publish is the fail-closed
checks inside `build.yml` and `ci_tools/`, which no agent is allowed to weaken.

There is no in-repository agent workflow to gate: an agent that holds a write
token here would be a second trust boundary, so there is none
([SECURITY-AI.md](./SECURITY-AI.md#one-autonomous-path-and-it-is-hive)).

## What is asked, not enforced

These are real rules, and nothing mechanical stops an agent breaking them.
Review is what catches it.

- **Who may merge.** The ruleset needs no approval, because a sole maintainer
  cannot approve their own pull request, and the Hive's owner-merge gate binds
  only the Hive. So a token that can write contents could merge any green pull
  request through the API, one that touches an owner-merge path included. Only
  Claude Code sessions have the merge command denied.
- **Never weaken a fail-closed check.** [`AGENTS.md`](../AGENTS.md) section 0
  rule 1. No test can tell a relaxed guard from a fixed cause.
- **The safety-critical statement.** [`CONTRIBUTING.md`](../CONTRIBUTING.md)
  asks for it, and the pull request template has a heading for it. Nothing
  fails when it is missing or empty.
- **Evidence in proportion to reach.** [`risk-tiers.md`](./risk-tiers.md) says
  how much evidence each tier needs. The `area/*` labels describe the tier;
  they do not block a merge. For the owner-merge paths the Hive's gate above
  leaves the merge to a person, but it checks who merges, not what the review
  covered.
- **No approval labels from automation.** The settings file denies creating,
  editing or deleting a label. Adding one to a pull request with `gh pr edit`
  only prompts.
  [`SECURITY-AI.md`](./SECURITY-AI.md#labels-carry-authority--automation-must-not-apply-them)
  explains why.

## Why CODEOWNERS lists only the owner-merge paths

Owner decision 2026-11-23 (Danathar): ordinary changes merge without the
owner's review; only image signing, workflows, the rulesets and `CODEOWNERS`
still need the owner. Workflows are on the list because the
`production-signing` environment has a branch policy but no required reviewer,
so any workflow on `main` can ask for it and be handed the cosign key. That
makes every workflow part of the signing path. The ruleset sets
`require_code_owner_review` to `false` and needs no other approval, so
`CODEOWNERS` no longer blocks a merge.
[`.github/CODEOWNERS`](../.github/CODEOWNERS) lists the paths the Hive's
`human_merge_paths` setting lists, and nothing else, so GitHub asks the owner
to review a pull request that touches one. The Hive is what holds such a pull
request for a person to merge. A catch-all `*` line would ask the owner to
review every pull request.

Renovate still merges its own patch, minor, digest and pin updates, through a
bypass that can only merge a pull request and cannot skip the required check.
AGENTS.md section 9, "Owner's standing decisions", already approves those
updates with green CI.
[`branch-protection.md`](./branch-protection.md#renovate-merges-its-own-non-major-updates)
explains how it is set up and what still waits for the owner.

When there is a second reviewer,
[`branch-protection.md`](./branch-protection.md#when-there-is-a-second-reviewer)
is where the ruleset changes, and the second reviewer joins the `CODEOWNERS`
lines.

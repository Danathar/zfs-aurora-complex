# Documentation Guide

If a term is unfamiliar, check the shared glossary first:
[`docs/glossary.md`](./glossary.md)

## Purpose

This page is the map of the documentation itself: what each document is for,
who should read it, and in what order.

The documentation in this repo is intentionally written for someone who is
learning these build, packaging, and GitHub workflow concepts while reading.
When practical, terms are explained where they first appear, and the glossary
fills in the rest.

## Documentation Tree

```text
README.md
CONTRIBUTING.md                 <- how to submit a change: tests, ruff, coverage floors, the safety-critical statement
AGENTS.md / CLAUDE.md / GEMINI.md <- rules for AI agents; section 0 (production) applies to everyone
docs/
  documentation-guide.md      <- this file (doc map + reading paths)
  glossary.md                 <- shared term and command definitions
  install-and-verify.md       <- switching a machine onto the image, post-boot checks, signature verification
  safety-model.md             <- what the pipeline promises, what it does not, and the rollback recovery policy
  building-locally.md         <- native build flow, local podman build, running the workflows in a fork, changing the base image
  licensing.md                <- CDDL/GPLv2 position on the published binary module
  code-reading-guide.md       <- repository layout, workflow map, step-by-step code reading order
  architecture-overview.md    <- high-level design and flow
  signing-and-bootc.md        <- image signing, bootc policy, and cosign compatibility
  upstream-change-response.md <- incident triage and recovery actions
  design-deep-dive.md         <- deep technical design + issue history
  akmods-fork-maintenance.md  <- how akmods source refs are selected and pinned
  maintenance-watchlist.md    <- pins and decisions on a clock that no automation watches
  sibling-delta.md            <- which shared ci_tools/ modules and build steps differ from the sister repository, and why
  production-boundary-proposal.md <- main protection and signing-key isolation (adopted)
  runtime-validation-proposal.md  <- boot/pool testing in CI (declined; rollback is the mitigation)
  risk-tiers.md               <- Tier 3/2/1/0: how much scrutiny a path gets, and the area/* labels
  review-rubric.md            <- what a review checks, in the order things go wrong
  quality.md                  <- what each badge, gate and refusal means, and what none of them proves
  metrics.md                  <- how to get the numbers (gh commands) and what they are worth
  metrics/                    <- dated readings of metrics.md, one file per day, left as read
  reflections/                <- retrospective lessons; README.md says what earns an entry
  SECURITY-AI.md              <- what an AI agent may do unattended, and which inputs are hostile
  agent-boundaries.md         <- the agent permission boundary: what is enforced and what is only asked
  branch-protection.md        <- the ruleset on main and how to check it is still applied
  multi-agent.md              <- which agents appear, how work reaches them, who merges
  agent-tasks/                <- README.md: tracing a change back to its task
  ai-ops-runbook.md           <- a signal or an agent's output looks wrong: what to do first
  strategy.md                 <- the goal, and the commands that show progress toward it
.github/
  pull_request_template.md    <- the risk tier, safety-critical statement and verification a pull request fills in
  copilot-instructions.md     <- GitHub Copilot's pointer at AGENTS.md section 0
.github/scripts/
  README.md                   <- workflow step -> command-line interface (CLI) command -> Python module map
.github/prompts/
  README.md                   <- reusable agent prompts: diagnose a build, replay a build, review a safety-critical change
.claude/commands/
  README.md                   <- the same procedures as Claude Code slash commands
.claude/memory/
  README.md                   <- corrected beliefs (corrections.md): what someone believed that was false
.claude/
  session-summary.md          <- state carried between agent sessions; not a changelog
tests/e2e/
  README.md                   <- the CLI run as a real subprocess: what it covers and what it does not
```

## What To Read First (By Goal)

### Goal: I am new and want the big picture

1. [`README.md`](../README.md)
2. [`docs/glossary.md`](./glossary.md)
3. [`docs/architecture-overview.md`](./architecture-overview.md)
4. [`docs/signing-and-bootc.md`](./signing-and-bootc.md)

### Goal: I just want to run this image

1. [`docs/install-and-verify.md`](./install-and-verify.md)
2. [`docs/safety-model.md`](./safety-model.md) -- especially the recovery policy
3. [`docs/licensing.md`](./licensing.md) if you intend to redistribute

### Goal: I want to build it or fork it myself

1. [`docs/building-locally.md`](./building-locally.md)
2. [`docs/akmods-fork-maintenance.md`](./akmods-fork-maintenance.md)
3. [`AGENTS.md`](../AGENTS.md) section 0 -- a fork's workflows can publish and sign

### Goal: I want to understand the code end-to-end

1. [`docs/code-reading-guide.md`](./code-reading-guide.md)
2. [`.github/scripts/README.md`](../.github/scripts/README.md)
3. [`docs/design-deep-dive.md`](./design-deep-dive.md)

### Goal: A workflow run failed and I need recovery steps

1. [`docs/upstream-change-response.md`](./upstream-change-response.md)
2. [`docs/signing-and-bootc.md`](./signing-and-bootc.md)
3. [`docs/design-deep-dive.md`](./design-deep-dive.md)

### Goal: I need to update the akmods source pin

1. [`docs/akmods-fork-maintenance.md`](./akmods-fork-maintenance.md)
2. [`docs/upstream-change-response.md`](./upstream-change-response.md)

### Goal: I want to submit a change

1. [`CONTRIBUTING.md`](../CONTRIBUTING.md)
2. [`AGENTS.md`](../AGENTS.md) section 0 -- this is production
3. [`docs/risk-tiers.md`](./risk-tiers.md) to see how much scrutiny your paths get
4. [`docs/review-rubric.md`](./review-rubric.md) for what a reviewer will check
5. [`tests/e2e/README.md`](../tests/e2e/README.md) if you are changing a CLI command

### Goal: I want to know what the badges, gates and numbers mean

1. [`docs/quality.md`](./quality.md) -- what each signal means and what none proves
2. [`docs/metrics.md`](./metrics.md) -- the commands, and what the numbers are worth
3. [`docs/metrics/`](./metrics/2026-10-11.md) -- dated readings
4. [`docs/strategy.md`](./strategy.md) -- the goal, and how far along it is

### Goal: I am an AI agent, or I am working with one

1. [`AGENTS.md`](../AGENTS.md) section 0, then [`CLAUDE.md`](../CLAUDE.md)
2. [`docs/SECURITY-AI.md`](./SECURITY-AI.md) -- what an agent may do unattended
3. [`docs/agent-boundaries.md`](./agent-boundaries.md) -- what is enforced, what is only asked
4. [`docs/risk-tiers.md`](./risk-tiers.md)
5. [`.github/prompts/README.md`](../.github/prompts/README.md) and
   [`.claude/commands/README.md`](../.claude/commands/README.md) -- the recurring procedures
6. [`docs/multi-agent.md`](./multi-agent.md) -- which agents appear, and who merges
7. [`docs/agent-tasks/README.md`](./agent-tasks/README.md) -- tracing a change to its task

### Goal: a bot or an agent did something and I am not sure it is right

1. [`docs/ai-ops-runbook.md`](./ai-ops-runbook.md) -- the first thing to do, per signal
2. [`docs/review-rubric.md`](./review-rubric.md)
3. [`docs/branch-protection.md`](./branch-protection.md) -- what the ruleset on `main` does and does not stop

### Goal: I want to know why things are the way they are

1. [`docs/reflections/README.md`](./reflections/README.md) -- lessons from things that went wrong
2. [`docs/production-boundary-proposal.md`](./production-boundary-proposal.md)
3. [`docs/runtime-validation-proposal.md`](./runtime-validation-proposal.md)

### Goal: I want to know what might silently go stale over time

1. [`docs/maintenance-watchlist.md`](./maintenance-watchlist.md)
2. [`docs/sibling-delta.md`](./sibling-delta.md) -- shared build scripts where one
   repository has a fix the other does not

## Where To Put New Documentation

1. Put shared term definitions in [`docs/glossary.md`](./glossary.md).
2. Put newcomer overview content in [`README.md`](../README.md). Keep it short:
   the README is an entry point and a router, not a manual. Long-form content
   belongs in one of the documents below.
3. Put operator steps (install, validate, verify) in
   [`docs/install-and-verify.md`](./install-and-verify.md).
4. Put design reasoning in [`docs/architecture-overview.md`](./architecture-overview.md).
5. Put signing and bootc trust details in [`docs/signing-and-bootc.md`](./signing-and-bootc.md).
6. Put runbook and incident-response steps in [`docs/upstream-change-response.md`](./upstream-change-response.md).
   Put the lookup from an automated signal or an agent's output to "what to do
   first" in [`docs/ai-ops-runbook.md`](./ai-ops-runbook.md), which links to the
   steps rather than copying them.
7. Put deeper workflow history and validation notes in [`docs/design-deep-dive.md`](./design-deep-dive.md).
8. Put workflow-step-to-code mapping in [`.github/scripts/README.md`](../.github/scripts/README.md).
9. Put pins or decisions that will need a future human call, and that no automation
   will surface, in [`docs/maintenance-watchlist.md`](./maintenance-watchlist.md).
   Put whether a shared `ci_tools/` module or `.github/actions/` build step still
   matches the sister repository's copy in [`docs/sibling-delta.md`](./sibling-delta.md).
10. Put how to submit and test a change in [`CONTRIBUTING.md`](../CONTRIBUTING.md),
    and what a reviewer checks in [`docs/review-rubric.md`](./review-rubric.md).
11. Put which paths carry which scrutiny in [`docs/risk-tiers.md`](./risk-tiers.md),
    and what an agent may do in [`docs/SECURITY-AI.md`](./SECURITY-AI.md) and
    [`docs/agent-boundaries.md`](./agent-boundaries.md). Put the ruleset on `main` in
    [`docs/branch-protection.md`](./branch-protection.md).
12. Put what a signal means in [`docs/quality.md`](./quality.md), how to get a number
    in [`docs/metrics.md`](./metrics.md), and a dated reading in `docs/metrics/`
    (one file per day; leave it as it was read).
13. Put a lesson learned from something that went wrong in `docs/reflections/`, as a
    new dated file ([`README.md`](./reflections/README.md) there says what earns
    one). Put a short corrected belief in `.claude/memory/corrections.md` instead.
14. Put which agents appear, and who merges, in [`docs/multi-agent.md`](./multi-agent.md);
    how to trace a change to its task in [`docs/agent-tasks/README.md`](./agent-tasks/README.md);
    and the project's goal and how to read progress in [`docs/strategy.md`](./strategy.md).
15. Add every new document to the tree above and to the router table in
    [`README.md`](../README.md) in the same change.

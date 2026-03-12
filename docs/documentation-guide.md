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
docs/
  documentation-guide.md      <- this file (doc map + reading paths)
  glossary.md                 <- shared term and command definitions
  code-reading-guide.md       <- step-by-step code reading order
  architecture-overview.md    <- high-level design and flow
  upstream-change-response.md <- incident triage and recovery actions
  zfs-kinoite-testing.md      <- deep technical design + issue history
  akmods-fork-maintenance.md  <- how to maintain the pinned akmods fork
.github/scripts/
  README.md                   <- workflow step -> command-line interface (CLI) command -> Python module map
```

## What To Read First (By Goal)

### Goal: I am new and want the big picture

1. [`README.md`](../README.md)
2. [`docs/glossary.md`](./glossary.md)
3. [`docs/architecture-overview.md`](./architecture-overview.md)

### Goal: I want to understand the code end-to-end

1. [`docs/code-reading-guide.md`](./code-reading-guide.md)
2. [`.github/scripts/README.md`](../.github/scripts/README.md)
3. [`docs/zfs-kinoite-testing.md`](./zfs-kinoite-testing.md)

### Goal: A workflow run failed and I need recovery steps

1. [`docs/upstream-change-response.md`](./upstream-change-response.md)
2. [`docs/zfs-kinoite-testing.md`](./zfs-kinoite-testing.md)

### Goal: I need to update the akmods source pin

1. [`docs/akmods-fork-maintenance.md`](./akmods-fork-maintenance.md)
2. [`docs/upstream-change-response.md`](./upstream-change-response.md)

## Where To Put New Documentation

1. Put shared term definitions in [`docs/glossary.md`](./glossary.md).
2. Put newcomer overview content in [`README.md`](../README.md).
3. Put design reasoning in [`docs/architecture-overview.md`](./architecture-overview.md).
4. Put runbook and incident-response steps in [`docs/upstream-change-response.md`](./upstream-change-response.md).
5. Put deeper workflow history and validation notes in [`docs/zfs-kinoite-testing.md`](./zfs-kinoite-testing.md).
6. Put workflow-step-to-code mapping in [`.github/scripts/README.md`](../.github/scripts/README.md).

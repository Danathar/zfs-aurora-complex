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
  install-and-verify.md       <- switching a machine onto the image, post-boot checks, signature verification
  safety-model.md             <- what the pipeline promises, what it does not, and the rollback recovery policy
  building-locally.md         <- native build flow, local podman build, running the workflows in a fork, changing the base image
  licensing.md                <- CDDL/GPLv2 position on the published binary module
  code-reading-guide.md       <- repository layout, workflow map, step-by-step code reading order
  architecture-overview.md    <- high-level design and flow
  signing-and-bootc.md        <- image signing, bootc policy, and cosign compatibility
  upstream-change-response.md <- incident triage and recovery actions
  zfs-aurora-testing.md       <- deep technical design + issue history
  akmods-fork-maintenance.md  <- how akmods source refs are selected and pinned
  maintenance-watchlist.md    <- pins and decisions on a clock that no automation watches
.github/scripts/
  README.md                   <- workflow step -> command-line interface (CLI) command -> Python module map
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

### Goal: I want to understand the code end-to-end

1. [`docs/code-reading-guide.md`](./code-reading-guide.md)
2. [`.github/scripts/README.md`](../.github/scripts/README.md)
3. [`docs/zfs-aurora-testing.md`](./zfs-aurora-testing.md)

### Goal: A workflow run failed and I need recovery steps

1. [`docs/upstream-change-response.md`](./upstream-change-response.md)
2. [`docs/signing-and-bootc.md`](./signing-and-bootc.md)
3. [`docs/zfs-aurora-testing.md`](./zfs-aurora-testing.md)

### Goal: I need to update the akmods source pin

1. [`docs/akmods-fork-maintenance.md`](./akmods-fork-maintenance.md)
2. [`docs/upstream-change-response.md`](./upstream-change-response.md)

### Goal: I want to know what might silently go stale over time

1. [`docs/maintenance-watchlist.md`](./maintenance-watchlist.md)

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
7. Put deeper workflow history and validation notes in [`docs/zfs-aurora-testing.md`](./zfs-aurora-testing.md).
8. Put workflow-step-to-code mapping in [`.github/scripts/README.md`](../.github/scripts/README.md).
9. Put pins or decisions that will need a future human call, and that no automation
   will surface, in [`docs/maintenance-watchlist.md`](./maintenance-watchlist.md).

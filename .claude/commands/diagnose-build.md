---
description: Name why a production run is red, with evidence, and say which fix applies
---

Follow [`.github/prompts/diagnose-build-failure.prompt.md`](../../.github/prompts/diagnose-build-failure.prompt.md).

That file is the procedure; do not restate it here. In particular:

- Read the failing step's stderr line before forming a theory. A known refusal
  exits 1 with one line, and that line is the diagnosis.
- Match the message against the guard table before proposing anything. Five
  of the entries have no fix in this repository, and a digest mismatch on
  `:latest` is an escalation, not a patch.
- `unexpected EOF` during a blob transfer is a registry or CDN failure, not a
  repository failure. Say so and leave the re-run to the maintainer — a re-run
  of `build.yml` publishes.
- Do not propose relaxing a guard, adding a fallback, or making a check
  best-effort to get past it. Stop and report instead.

Run number or URL, if given: $ARGUMENTS

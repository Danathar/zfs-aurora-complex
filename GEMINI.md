# GEMINI.md

Behavioral guidelines adapted from `CLAUDE.md` for Gemini 3.1 Pro Preview. Merge with project-specific instructions as needed.

Source basis: https://github.com/forrestchang/andrej-karpathy-skills

Intent: make goals, constraints, and verification explicit so the model stays precise and avoids speculative edits.

**Tradeoff:** These guidelines bias toward correctness and explicitness over speed. For trivial tasks, use judgment.

## 0. This repository is in production

This is not a sandbox. The maintainer daily-drives the image this repo publishes, on real
hardware, with multi-terabyte ZFS pools attached. A bad image reaching `:latest` gets
pulled by `bootc upgrade` onto a machine someone depends on — there is no staging tier in
between. The bar for changes here is higher than "tests pass," and these constraints
override sections 1-5 below when they conflict:

- **Never weaken a fail-closed check to make something pass.** This codebase deliberately
  fails the build when ZFS does not match the primary kernel, when a signature cannot be
  verified, when a digest does not match, or when the akmods cache does not cover the
  required kernel and ZFS line. Fix the underlying cause or stop and report it — do not
  relax the check, add a fallback, or make it best-effort.
- **Treat the build, promotion, and signing path as safety-critical**: `build.yml`,
  `.github/actions/publish-native-image`, `ci_tools/sign_image.py`,
  `ci_tools/promote_stable.py`, `ci_tools/check_akmods_cache.py`,
  `containerfiles/zfs-akmods/install_zfs_from_akmods_cache.py`, and
  `files/scripts/configure_signing_policy.py`. State explicitly what could reach a booted
  machine if a change to one of these is wrong.
- **Verify claims against reality, not memory or docs.** Docs here have drifted from the
  code before. Read the code, check a real CI run's log, or inspect the actual published
  artifact before asserting how something behaves. If you cannot verify a claim, say so.
- **A green pipeline is not a safe image.** A successful run proves the build completed,
  not that the result is safe to boot. State which one you actually checked.
- **ZFS changes carry data-loss risk.** Anything touching the ZFS version, the kernel it
  is built against, or pool-facing behavior must consider rollback: an image that
  activates newer pool features can make the previous image unable to import those pools.
  Surface that risk; never quietly bump a ZFS line.
- **Do not push, promote, tag, or delete published artifacts on your own initiative.**
  Registry tags and git history here are outward-facing. Propose; let the maintainer decide.
- **When unsure whether something is risky, stop and ask.** An interrupted task is cheap
  here; a bad published image is not.

## Preferred Task Shape

For non-trivial work, anchor the response around this structure:

```text
Goal: [what success looks like]
Constraints: [what must not change]
Assumptions: [only if they matter]
Plan:
1. [step] -> verify: [check]
2. [step] -> verify: [check]
Result: [what changed / what remains]
```

If the task is simple, compress the format, but keep the same thinking.

## 1. Clarify Before Coding

**Resolve ambiguity early. Do not improvise on unclear requirements.**

Before implementing:
- Restate the request in concrete terms.
- State assumptions that affect behavior, file scope, or user-visible output.
- If multiple reasonable interpretations would lead to different code, surface them instead of silently choosing one.
- If the requested solution seems heavier than necessary, say so and propose the simpler path.
- If something important is unclear, stop and ask a focused question.

## 2. Prefer the Smallest Correct Change

**Solve the asked problem with the least code and the fewest touched files.**

- Do not add features, flags, abstractions, or future-proofing that were not requested.
- Prefer existing helpers and established patterns over new layers.
- Match the repository's current style, naming, and structure.
- Keep diffs reviewable. Every changed line should map back to the user's request.

Ask yourself: "Would a senior engineer call this overbuilt for the problem?" If yes, simplify it.

## 3. Edit Surgically

**Preserve surrounding behavior unless the task explicitly says otherwise.**

When editing existing code:
- Do not refactor adjacent code just because you noticed it.
- Do not reformat files unnecessarily.
- Remove only the unused code created by your own change.
- Mention unrelated issues you notice, but do not fix them opportunistically.

When touching tests:
- Prefer the narrowest test that proves the requested behavior.
- Do not rewrite test structure unless it is required for the fix.

## 4. Make Success Verifiable

**Turn requests into checks you can run.**

Examples:
- "Fix the bug" -> reproduce it, change the code, re-run the reproducer.
- "Add validation" -> add tests for invalid inputs, then make them pass.
- "Refactor X" -> confirm behavior stays the same before and after.

Before finishing:
- Run the smallest meaningful verification available.
- If you could not verify, say exactly why.
- Call out remaining assumptions or risk briefly.

## 5. Report Outcomes Explicitly

**End with outcome, verification, and any unresolved risk.**

A good closeout usually includes:
- what changed
- what was verified
- what was not verified
- anything still uncertain

---

**These guidelines are working if:** diffs stay small, assumptions are visible, verification is concrete, and follow-up corrections decrease.

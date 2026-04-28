# GEMINI.md

Behavioral guidelines adapted from `CLAUDE.md` for Gemini 3.1 Pro Preview. Merge with project-specific instructions as needed.

Source basis: https://github.com/forrestchang/andrej-karpathy-skills

Intent: make goals, constraints, and verification explicit so the model stays precise and avoids speculative edits.

**Tradeoff:** These guidelines bias toward correctness and explicitness over speed. For trivial tasks, use judgment.

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

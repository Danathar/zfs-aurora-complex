# CLAUDE.md

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

Source: https://github.com/forrestchang/andrej-karpathy-skills

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

---

## 0. This Repository Is In Production — Read This First

**This is not a sandbox or a teaching demo. The maintainer daily-drives the image
this repo publishes, on real hardware, with multi-terabyte ZFS pools attached.**

What that means concretely:

- A bad image that reaches `:latest` gets pulled by `bootc upgrade` onto a
  machine someone works on. There is no staging tier between this repo and that
  machine.
- A ZFS module that builds but misbehaves sits between the user and pooled data.
  "It compiled" is not evidence that it is safe.
- A broken promotion or signing path can either strand a machine on an
  unverifiable update or move `:latest` to something untested.

So the bar for changes here is higher than "tests pass."

### Rules that override the general guidance below

1. **Never weaken a fail-closed check to make something pass.** This codebase
   deliberately fails the build when ZFS does not match the primary kernel, when
   a signature cannot be verified, when a digest does not match, or when the
   akmods cache does not cover the required kernel and ZFS line. If one of these
   fires, the correct response is to fix the underlying cause or stop and report
   it — never to relax the check, add a fallback, or make it best-effort.

2. **Treat the build, promotion, and signing path as safety-critical.** That is:
   `build.yml`, `.github/actions/publish-native-image`, `ci_tools/sign_image.py`,
   `ci_tools/promote_stable.py`, `ci_tools/check_akmods_cache.py`,
   `containerfiles/zfs-akmods/install_zfs_from_akmods_cache.py`, and
   `files/scripts/configure_signing_policy.py`. Changes to these need an explicit
   statement of what could reach a booted machine if the change is wrong.

3. **Verify claims against reality, not memory or docs.** Docs in this repo have
   drifted from the code before. Before asserting how something behaves, read the
   code, check a real CI run's logs, or inspect the actual published artifact.
   If you cannot verify a claim, say so plainly rather than presenting an
   inference as a fact.

4. **Distinguish "the pipeline is green" from "the image is good."** A successful
   run proves the build completed, not that the result is safe to boot. When
   reporting on a run, say which one you actually checked.

5. **Data-loss awareness for ZFS changes.** Anything touching the ZFS version,
   the kernel it is built against, or pool-facing behavior must consider
   rollback: an image that activates newer pool features can make the previous
   image unable to import those pools. Surface that risk explicitly; never
   quietly bump a ZFS line.

6. **Do not push, promote, tag, or delete published artifacts on your own
   initiative.** Registry tags and git history here are outward-facing. Propose;
   let the maintainer decide.

7. **When you are unsure whether something is risky, stop and ask.** In this
   repo, an interrupted task is cheap and a bad published image is not.

---

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

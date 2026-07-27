# AGENTS.md

Behavioral guidelines for GPT-based coding agents working in this repository. Mirrors `CLAUDE.md` but tightened for failure modes typical of GPT-family models (over-eagerness, scope creep, plausible-but-wrong API usage). Merge with project-specific instructions as needed.

Source inspiration: https://github.com/forrestchang/andrej-karpathy-skills

**Tradeoff:** These rules bias toward caution and verifiability over speed. For trivial tasks (typo fix, single-line rename), use judgment.

---

## 0. This repository is in production — read this before anything else

**This is not a sandbox or a teaching demo.** The maintainer daily-drives the image this
repo publishes, on real hardware, with multi-terabyte ZFS pools attached. There is no
staging tier between this repo and that machine.

Concretely:

- A bad image that reaches `:latest` gets pulled by `bootc upgrade` onto a machine
  someone works on.
- A ZFS module that builds but misbehaves sits between the user and pooled data.
  "It compiled" is not evidence that it is safe — GPT-family models in particular tend to
  treat a green CI run as proof of correctness. It is not; see rule 4 below.
- A broken promotion or signing path can strand a machine on an unverifiable update, or
  move `:latest` to something untested.

The bar for changes here is higher than "tests pass." These rules override the general
guidance in sections 1–7 below when they conflict:

1. **Never weaken a fail-closed check to make something pass.** This codebase
   deliberately fails the build when ZFS does not match the primary kernel, when a
   signature cannot be verified, when a digest does not match, or when the akmods cache
   does not cover the required kernel and ZFS line. If one of these fires, fix the
   underlying cause or stop and report it. Do not relax the check, add a fallback, or make
   it best-effort — this is exactly the kind of "helpful" robustness section 3 (Simplicity
   First) tells you to resist, and it is more important here than usual.
2. **Treat the build, promotion, and signing path as safety-critical.** That is:
   `build.yml`, `.github/actions/publish-native-image`, `ci_tools/sign_image.py`,
   `ci_tools/promote_stable.py`, `ci_tools/check_akmods_cache.py`,
   `containerfiles/zfs-akmods/install_zfs_from_akmods_cache.py`, and
   `files/scripts/configure_signing_policy.py`. Changes to these need an explicit
   statement of what could reach a booted machine if the change is wrong — add this to
   the confirmation you already give in section 1 below.
3. **Verify claims against reality, not memory or docs.** Docs in this repo have drifted
   from the code before. Before asserting how something behaves, read the code, check a
   real CI run's logs, or inspect the actual published artifact. Mark it `[unverified]`
   per section 1's assumption-marking convention if you have not.
4. **Distinguish "the pipeline is green" from "the image is good."** A successful run
   proves the build completed, not that the result is safe to boot. When reporting on a
   run, say which one you actually checked.
5. **Data-loss awareness for ZFS changes.** Anything touching the ZFS version, the kernel
   it is built against, or pool-facing behavior must consider rollback: an image that
   activates newer pool features can make the previous image unable to import those
   pools. Surface that risk explicitly. Never quietly bump a ZFS line.
6. **Do not push, promote, tag, or delete published artifacts on your own initiative.**
   Registry tags and git history here are outward-facing. Propose; let the maintainer
   decide.
7. **When you are unsure whether something is risky, stop and ask.** An interrupted task
   is cheap here. A bad published image is not.

---

## 1. Before you start (general task confirmation)

Confirm the following out loud, in one paragraph, before writing any code:

1. Restate the task in your own words.
2. List your assumptions. Mark each `[verified]` (you read the file or ran the command) or `[unverified]` (you are guessing).
3. List the files you intend to create or modify. Nothing not on this list may change.
4. State the success criteria as commands or checks (e.g., `pytest tests/test_foo.py passes`, `grep -q 'X' file.yml`).

If any item is `[unverified]`, do the verification step before continuing — do not implement on top of guesses.

---

## 2. Think before coding

**Do not assume. Do not hide confusion. Surface tradeoffs.**

- State assumptions explicitly. If uncertain, stop and ask one specific question.
- If multiple interpretations exist, list them and ask which to pick. Do not silently choose.
- If a simpler approach exists, propose it before implementing the complex one.
- If something is unclear, name what is confusing and ask. Do not invent.

**Anti-pattern to avoid:** confidently invoking a function, flag, or API that you have not seen in the repo or in linked official docs. If you cannot point to a file or URL where the API is defined, do not call it.

---

## 3. Simplicity first

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code. No helper classes, no "for future flexibility."
- No configuration knobs unless explicitly requested.
- No error handling for impossible scenarios. Trust internal callers.
- If your solution is 200 lines and could be 50, rewrite it.

**GPT-specific reminder:** resist the urge to add "robustness" — retries, fallbacks, defensive `try/except`, extra logging — unless the task requires them. Default to the boring direct path.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

---

## 4. Surgical changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Do not "improve" adjacent code, comments, formatting, or imports.
- Do not refactor things that are not broken.
- Match existing style even if you would write it differently.
- If you notice unrelated dead code or bugs, mention them in your final reply. Do not fix them.

When your changes create orphans:
- Remove imports, variables, or functions that YOUR changes made unused.
- Do not remove pre-existing dead code unless explicitly asked.

**The test:** every changed line must trace directly to the user's request. If you cannot justify a line by pointing to the prompt, delete it.

---

## 5. Goal-driven execution

**Define success criteria up front. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan and execute it:

```
1. [Step]  → verify: [exact command or check]
2. [Step]  → verify: [exact command or check]
3. [Step]  → verify: [exact command or check]
```

After each step, run the verification before moving on. If verification fails, stop and report. Do not silently continue with broken state.

**Strong success criteria let you loop independently.** Weak criteria ("make it work") require constant back-and-forth.

---

## 6. Tooling discipline (this repo)

- Python CI logic lives in [ci_tools/](ci_tools/) and is invoked through [ci_tools/cli.py](ci_tools/cli.py). Add new commands by registering them there and writing a paired test under [tests/](tests/).
- Workflow YAML lives in [.github/workflows/](.github/workflows/); reusable pieces go in [.github/actions/](.github/actions/) as composite actions.
- Do not edit `cosign.key` (gitignored anyway) or alter signing logic without explicit instruction.
- Run `pytest` from repo root. Tests must pass before you declare success.
- Match the existing style in `ci_tools/`: small modules, `argparse`, write outputs to `GITHUB_OUTPUT` rather than stdout-parsing.

---

## 7. Reporting back

When finished:
1. List the files you changed (paths only).
2. State which success criteria pass and how you verified them.
3. List anything you noticed but did not change.
4. List anything you skipped or could not do, and why.

Do not pad the report with summaries of code you wrote — the diff speaks for itself. Do not claim verification you did not perform.

---

**These guidelines are working if:** diffs stay small, no invented APIs appear, verification commands are run before "done", and clarifying questions come before implementation rather than after mistakes.

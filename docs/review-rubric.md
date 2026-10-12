# Review rubric

What to check on a pull request here, in the order things actually go wrong.

This is not a style checklist. `ruff` and `.editorconfig` handle style, and CI
runs the tests. The questions below are the ones a machine cannot answer, and
they are ordered so the disqualifying ones come first: there is no point
weighing a design trade-off on a change that weakens a guard.

This repository is in production. A merge to `main` that touches any
non-Markdown file starts `build.yml`, which builds, signs and can move `:latest`
on a machine someone works on, with no staging tier in between
([`AGENTS.md`](../AGENTS.md) section 0). Review is therefore the main place a
bad change is caught, and "CI is green" is the weakest evidence in it.

For a change touching the signing or promotion path, use
[`.github/prompts/review-safety-critical-change.prompt.md`](../.github/prompts/review-safety-critical-change.prompt.md),
which is this rubric's section 1 expanded into a procedure. How much of the
rubric a change needs depends on its tier in [`risk-tiers.md`](./risk-tiers.md).

## How to read the verdicts

| Verdict | Meaning |
| --- | --- |
| **Blocking** | The pull request does not merge in this form. Not a trade-off to weigh against the benefit. |
| **Answer required** | The change may be right, but it cannot be judged until the author answers. |
| **Comment** | Worth saying; not worth holding the change for. |

An honest "I could not verify this" is a complete review outcome. It is better
than a confident inference, and AGENTS.md section 0 rule 3 exists because
inferences here have been wrong before.

## 1. Was a fail-closed check weakened? — *Blocking*

The first question, and the one most likely to be answered wrongly, because the
weakening usually looks like robustness.

Look for:

- A `raise` that became a warning, a `print`, or a `return`.
- A comparison that got looser: exact digest → prefix, `==` → `in`, an exact
  version → a range or a glob.
- A new fallback, default, or `or`-clause on a path that previously refused.
- `|| true`, `continue-on-error`, or `if: always()` added to a checking step.
- A test changed so a guard stops being exercised.
- A floor lowered in `.coverage-thresholds.json` with no reason in the commit
  message.
- A timeout or retry added around a check so it eventually passes.
- A credential made optional on a registry call. Anonymous access to this
  repository's own public package works, which is exactly how an unauthenticated
  path stays invisible until it meets a private one
  ([#64](https://github.com/Danathar/zfs-aurora-complex/pull/64)).

If any of these is present, that **is** the review. The correct response is the
underlying cause (AGENTS.md section 0 rule 1). "The guard was too strict" is a
claim that needs evidence about the guard, not about the inconvenience. The
scheduled build refusing a ZFS that cannot be built for the kernel is the guard
working: it lost 13 daily refreshes in July 2026 and moved nothing onto a machine
([`docs/metrics/2026-10-11.md`](./metrics/2026-10-11.md)).

## 2. Does the safety-critical statement exist and say something? — *Blocking if missing*

Required by [`CONTRIBUTING.md`](../CONTRIBUTING.md) when the diff touches any of
the seven files in AGENTS.md section 0 rule 2: `build.yml`,
`.github/actions/publish-native-image`, `ci_tools/sign_image.py`,
`ci_tools/promote_stable.py`, `ci_tools/check_akmods_cache.py`,
`containerfiles/zfs-akmods/install_zfs_from_akmods_cache.py` and
`files/scripts/configure_signing_policy.py`. The pull request template has a
section for it, and the `area/safety-critical` label (from the path labeler, see
[`risk-tiers.md`](./risk-tiers.md)) marks the pull requests that need one.

The statement has to answer *what could reach a booted machine if this change is
wrong*, not "this is low risk" and not "tests pass". It should also say that the
merge starts a production `build.yml` run if any non-Markdown file is in the diff.

"Not applicable, and here is why" is a valid answer. An absent section is not.

## 3. Rollback and pool safety — *Blocking if unaddressed*

Applies if the change touches the ZFS version, the kernel it builds against, or
anything pool-facing.

An image that activates newer on-disk pool features can leave the previous image
unable to import those pools, which breaks the rollback path the whole safety
model rests on. The author has to say so explicitly. `renovate.json` already
refuses to automerge an OpenZFS minor-line bump for this reason; a hand-written
change deserves the same scrutiny. Also check that the claim is about what this
repository controls: the Aurora DX base image, not this repo, sets the ZFS
userspace line
([`safety-model.md`](./safety-model.md#the-zfs-line-is-set-by-the-base-image-not-by-this-repo)), and a
change that assumes otherwise can build and still not install
([`docs/reflections/`](./reflections/README.md)).

## 4. Are the claims verified or inferred? — *Answer required*

Docs here have drifted from the code before, so a pull request body asserting how
something behaves is a claim to check, not evidence.

- If it cites a CI run, does it say **which** run? A green pipeline proves the
  build completed, not that the image is good.
- If it describes existing behavior, was the code read, or the doc describing
  the code?
- If it quotes a file path, line, or message, does it still match?
- If it says a change was validated, was that validation on the thing that
  merges? A green run last week on different code proves little: one rechunk run
  passed the day before an identical-code run failed (`maintenance-watchlist.md`).

A body that distinguishes "verified by X" from "I could not verify Y" is doing
this correctly and should be credited, not treated as weakness.

## 5. Do the tests test anything? — *Blocking for the first item*

- **`assertRaises(CiToolError)` with no message assertion.** It passes for any
  reason at all, including an unrelated missing environment variable, so it
  reports a guard as covered when the guard never ran. Pin the message or the
  specific guard.
- Does a mocked error path use the *real* failure shape? A registry timeout, a
  rate limit, a malformed manifest. A test that patches a call to raise agrees
  with whatever it was told.
- If the change adds a module, does it have an entry in
  `.coverage-thresholds.json`, either a floor or a reason it is unmeasured? CI
  fails without one, so this should already be answered, but check the floor is
  what the suite reaches rather than zero.
- Does a new test have a side effect outside a temporary directory? A test that
  can reach the network, a registry, or an absolute path such as `/tmp/akmods`
  is not a test (see [`.claude/memory/corrections.md`](../.claude/memory/corrections.md)).
- Do not wave through a red test as stale. In September 2026 two Renovate pull
  requests failed `test_container_literals_match_the_checked_in_default` because
  each bumped one of two places that must name the same digest. The test was
  right ([`docs/metrics/2026-10-11.md`](./metrics/2026-10-11.md)).

## 6. Scope — *Comment, unless it hides something*

`CONTRIBUTING.md`: every changed line traces to the change the pull request set
out to make; adjacent cleanups belong in their own pull request.

This is normally a comment. It becomes blocking when the unrelated change is in
a file from section 2's list, because a reformat in a signing path makes the
real diff unreadable.

## 7. Documentation that will drift — *Comment*

- Does a new document belong in `docs/documentation-guide.md`'s tree and the
  `README.md` router?
- Does the change make an existing sentence false? Editing a workflow often
  invalidates prose describing it, and this repository has landed that mistake
  before ([#49](https://github.com/Danathar/zfs-aurora-complex/pull/49) and
  [#58](https://github.com/Danathar/zfs-aurora-complex/pull/58) corrected docs
  that had fallen behind the code).
- Is the new prose a *second copy* of something? `AGENTS.md` is the single
  source; `.github/copilot-instructions.md`, `.cursor/rules/`, and
  `.claude/commands/` are pointers at it on purpose.
- A documentation-only pull request starts no build and no PR image check
  (`paths-ignore` on `**/*.md` and `docs/**`), so nothing but this review
  exercises it. Check its links and commands by running them.

## 8. Automated review findings — *Answer required*

`chatgpt-codex-connector[bot]` reviews pull requests here and its findings carry
a severity badge. They are *often* right, not automatically right.

Each finding needs one of: a fix, or a stated reason it does not apply. A
finding that is silently ignored is the one worth reading twice. The P1 on
[#62](https://github.com/Danathar/zfs-aurora-complex/pull/62) was correct: the
guidance told a future maintainer to validate a revert by dispatching `build.yml`
on `main`, which defaults to promoting `:latest`. It also arrived after #62 had
merged, so the fix needed its own pull request,
[#63](https://github.com/Danathar/zfs-aurora-complex/pull/63). Read the findings
before you merge, and re-check whether the pull request is still open before
pushing a fix to its branch.

## A minimal review, for a small change

Sections 1, 2 and 5 are never skippable. If a change touches no safety-critical
file, adds no test, and is documentation only, sections 4 and 7 are the review.
Say which sections you applied so the next reader knows what was and was not
looked at.

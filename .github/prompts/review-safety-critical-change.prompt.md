---
description: Review a diff that touches the build, promotion, or signing path
mode: agent
---

# Review a safety-critical change

Use this when a diff touches any of the seven files AGENTS.md section 0 rule 2
names:

- `.github/workflows/build.yml`
- `.github/actions/publish-native-image`
- `ci_tools/sign_image.py`
- `ci_tools/promote_stable.py`
- `ci_tools/check_akmods_cache.py`
- `containerfiles/zfs-akmods/install_zfs_from_akmods_cache.py`
- `files/scripts/configure_signing_policy.py`

**Goal:** answer one question — *what could reach a booted machine if this
change is wrong?* AGENTS.md section 0 rule 2 requires that statement for
any of these files; treat it as the review, not a formality.

## 1. Was any fail-closed check weakened?

This is the first question and it is disqualifying, not a trade-off. Look for:

- A `raise` that became a warning, a `print`, or a `return`.
- A comparison that became looser: exact digest to prefix, equality to `in`,
  a version match to a range.
- A new fallback, default, or `or`-clause on a path that previously refused.
- `|| true`, `continue-on-error`, `if: always()` added to a checking step.
- A test changed so a guard stops being exercised, or a per-module coverage
  floor lowered without a reason in the commit message.

If any of these is present, that is the review. The fix is the underlying
cause.

## 2. Trace the blast radius

For the specific change, say which of these it can affect and how:

- **Signing.** Can an image be published unsigned, or signed with the wrong
  key? Can verification be skipped?
- **Promotion.** Can `:latest` move to something that was not built and tested
  by this run? `promote_stable.py` re-reads the destination digest after the
  copy for exactly this reason.
- **The kmod.** Can a `kmod-zfs` built for a different kernel, or a different
  ZFS line, end up in the image? An image whose ZFS label disagrees with what
  it ships is worse than a failed build.
- **Rollback.** Does this change the ZFS line or anything pool-facing? An image
  that activates newer pool features can leave the previous image unable to
  import those pools — which breaks the recovery path the whole safety model
  rests on.

## 3. Check the claims, do not accept them

Docs in this repo have drifted from the code before (AGENTS.md section 0 rule
3). If the PR body asserts how something behaves, read the code, the run log,
or the published artifact. If you could not verify a claim, say so plainly
rather than repeating it.

For a change that touches promotion or signing, "the pipeline is green" is not
evidence. Say which run you read and what it actually proves.

## 4. What the review looks like

1. The blast-radius statement: what reaches a booted machine if this is wrong.
2. Whether any fail-closed check was weakened — yes or no, with the line.
3. Rollback impact, or "not applicable" with the reason.
4. What you verified, and what you could not.

An honest "I could not verify X" is a better review than a confident inference.

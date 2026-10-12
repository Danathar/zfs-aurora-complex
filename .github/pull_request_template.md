## What changed

<!-- The behavior change and why it is needed. -->

## Risk tier

<!--
Pick the highest tier any changed path falls in, using docs/risk-tiers.md:
Tier 1, Tier 2 or Tier 3. Tier 3 is the build, promotion and signing path and
the agent permission boundary. If a changed path is in no tier, say so.
-->

## Safety-critical statement

<!--
Required by CONTRIBUTING.md for Tier 3, and for any file named in AGENTS.md
section 0 rule 2: build.yml, .github/actions/publish-native-image,
ci_tools/sign_image.py, ci_tools/promote_stable.py,
ci_tools/check_akmods_cache.py,
containerfiles/zfs-akmods/install_zfs_from_akmods_cache.py, or
files/scripts/configure_signing_policy.py.

Say what could reach a booted machine if this change is wrong. This image is
daily-driven in production, so that statement is the review, not a formality.
Note that merging any non-Markdown file to main starts a build.yml run. Write
"Not applicable" if no Tier 3 path is touched.
-->

## Rollback impact

<!--
Required if this changes the ZFS line, the kernel it builds against, or
anything pool-facing: an image that activates newer pool features can leave
the previous image unable to import those pools. Write "Not applicable"
otherwise.
-->

## Checks

- [ ] `python3 -m pytest tests/ -v` passes
- [ ] `ruff check ci_tools/ shared/ tests/ files/ containerfiles/` is clean
- [ ] `python3 tests/check_coverage.py` passes after a run with
      `--cov-report=json` (see CONTRIBUTING.md, Coverage); a lowered floor
      has its reason in the commit message
- [ ] No fail-closed check was relaxed, given a fallback, or made best-effort
      (AGENTS.md section 0 rule 1)
- [ ] Every changed line traces to the change this PR set out to make; adjacent
      cleanups are not included

## Verification

<!--
What you actually ran or read, not what you expect to be true. If you are
reporting on a CI run, say which run. A green pipeline proves the build
completed, not that the image is good (AGENTS.md section 0 rule 4).

State anything you could not verify rather than presenting an inference as a
fact.
-->

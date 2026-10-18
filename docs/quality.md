# Quality

Where the signal about this repository actually comes from, and what each source
can and cannot tell you.

There is no dashboard service here. The dashboard is five status badges in
[`README.md`](../README.md), a handful of gates, and a set of fail-closed checks.
The useful thing to write down is what each one *means*, because the badges are
routinely misread and the most important gates are the ones that stop a bad image
rather than the ones that lint code.

[`docs/metrics.md`](./metrics.md) is the companion: this page is what the
signals mean, that one is how to get numbers.

**The limit on all of it.** Nothing in CI boots the image or imports a pool
before `:latest` moves. Every signal below is about whether the image was
composed correctly, signed, and built from matching inputs. None of them says it
works on a machine. [`safety-model.md`](./safety-model.md) states that boundary,
and [`runtime-validation-proposal.md`](./runtime-validation-proposal.md) records
the maintainer's decision, on 2026-07-28, to rely on image rollback rather than
add a runtime test tier. This page does not reopen that decision, but it does
refuse to let a green signal stand in for it. The closest signal is
`boot-test.yml`, which boots `:latest` in a VM after every `build.yml` run on
`main` and checks that ZFS loads; it has no badge, gates nothing, and creates no pool.

## The badges

| Badge | Source | Answers |
| --- | --- | --- |
| **build** | Actions status for `build.yml` on `main` | Did the last production run pass? |
| **tests** | Actions status for `test.yml` on `main` | Did ruff, the unit suite and the per-module coverage floors pass on `main`? |
| **nightly compliance** | Actions status for `nightly-compliance.yml` on `main` | Does the suite still pass, and does the published `:latest` still verify against the committed `cosign.pub`? |
| **last good build** | `status` branch payload written from the published `:latest` image | Is there a usable signed image, and how old is it? |
| **OpenZFS/kernel status** | `status` branch payload written by `akmods-failure-triage.yml` | If the build is red, is it because ZFS cannot be built for the current kernel? |

The README also carries Ask DeepWiki, Hive, ACMM, AI-assisted and licence badges. They
are not status signals, so they are not tabulated here. The ACMM and AI badges lead to the
README's "Maintained with Hive" and "About this project" sections.
Run history for the workflow badges is on the Actions tab (`gh run list --workflow test.yml`).

**Read the badges together.** A red **build** with a recent **last good build**
means the published image is still there and has not been refreshed. It does
**not** mean the image boots: the last-good badge is derived from the `Created`
field on the published image, so it reports the artifact's age and nothing else.
That is a very different situation from a red build with a stale last good
build, which means machines tracking `:latest` are drifting.

**A cancelled run reads as a failed one.** The **build** badge is GitHub's own,
and it reports the most recent *completed* run, where "completed" includes
cancelled. A cancelled run renders as `failing`, identically to a real failure.

That is not hypothetical here, because `build.yml` is built to cancel itself:

```yaml
concurrency:
  group: ${{ github.workflow }}-${{ github.ref || github.run_id }}
  cancel-in-progress: true
```

So **merging several pull requests to `main` in quick succession turns the badge
red**, and it stays red until the last run finishes. Each merge starts a run and
cancels the one before it. Nothing is wrong: the guard is doing exactly what its
comment says, never letting two `main` runs race to publish, and the signed
`:latest` from the last completed promotion is still there the whole time. The
2026-10-11 reading in [`docs/metrics/`](./metrics/2026-10-11.md) counts 15
cancelled push runs of `build.yml` in the repository's history; the command
`gh run list --workflow build.yml --event push --limit 1000 --json conclusion`
shows them.

Two ways to avoid inflicting it on yourself: space the merges out, or merge the
documentation-only pull requests last, since `build.yml`'s `paths-ignore`
(`README.md`, `**/README.md`, `docs/**`, `**/*.md`) means those start no run at
all.

Note the asymmetry with this repo's own badges. `write_akmods_badge.py`
deliberately refuses to let a cancelled or skipped run change the OpenZFS/kernel
badge. GitHub's build badge has no such rule, and this repo does not control it.

Two properties of the badge pipeline are deliberate:

- **It refuses to guess.** `write_akmods_badge.py` leaves the previous badge
  state untouched on any conclusion it cannot interpret, rather than overwriting
  it with something invented. A stale-but-true badge beats a fresh-but-wrong one,
  so a badge that has not moved is not automatically a badge that is working.
- **A skipped run cannot turn a red badge green.** A scheduled run that the
  stable-signal gate skips reports `success` without building anything. That rule
  is enforced inside `ci_tools/write_akmods_badge.py` (the `build_ran` check), not
  only in the workflow that calls it, so it stays correct if the workflow is
  edited.

## The gates

| Gate | Runs on | Blocks |
| --- | --- | --- |
| `Python Unit Tests` (`test.yml`) | every PR and push to `main` | **merging the pull request**, through the ruleset on `main`, not publishing. See below |
| `ruff check` | same job | same |
| Per-module coverage floors (`tests/check_coverage.py`) | same job | same |
| `Build PR Image (No Push)` (`build-pr.yml`) | pull requests that change something other than Markdown or `docs/**` | nothing by rule; it proves the image still builds. It stops before any push or signing step |
| `Evaluate Stable Signal Gate` (`build.yml`) | scheduled runs | *skips* the build on a scheduled run when upstream has not moved. A skip, not a failure. Push and manual runs always build |
| `check-akmods-cache` strict mode (`REQUIRE_MATCH`) | after an akmods rebuild, before the candidate build | the build, if the rebuilt cache does not carry a `kmod-zfs` for the primary kernel at the resolved ZFS version |
| `ci_tools/sign_image.py` under `python3 -I` (`sign-akmods-cache` and `sign-candidate-image` in `build.yml`) | after the push, before the candidate tag moves | publishing. The candidate tag moves to a digest only after that digest is signed and verified, and an unsigned production image is refused outright |
| `promote-stable` digest re-read | after the copy to `:latest` | the run, not the copy. It detects a bad promotion, it does not prevent one. See the warning below |
| `bootc container lint` (`Containerfile`) | every image build | the build, if `bootc container lint` rejects the image |
| `nightly-compliance.yml` | 05:00 UTC daily, and on dispatch | nothing. It reports. See below |
| `boot-test.yml` | after every `build.yml` run on `main`, and on dispatch | nothing. It boots `:latest` after promotion and reports |

> **A red `build.yml` does not prove `:latest` is untouched.** The candidate is
> pushed and signed before the promotion job runs, and `promote_stable.py`
> copies to `:latest` and *then* re-reads the destination digest to confirm the
> copy landed on what was signed. So a `Promoted digest mismatch` failure means
> the copy already happened: the run is red and `:latest` has moved. That ordering
> is deliberate and correct (you cannot verify a copy you have not made), but it
> means the guard reports a bad promotion rather than preventing one. **Check the
> published digest yourself before assuming**, with the command in
> [`install-and-verify.md`](./install-and-verify.md); do not infer it from the
> run's colour.

### The first three block a merge, not a publish

`main` has a ruleset that requires a pull request and a passing
`Python Unit Tests` before anything lands, and refuses a direct push.
[`branch-protection.md`](./branch-protection.md) explains each rule and how to
check that it is still applied. So a red `Python Unit Tests` stops the merge. It
does not stop a publish: `build.yml` does not run the unit suite, so the daily
scheduled build signs and promotes whatever is on `main` without it.

The rows from `Evaluate Stable Signal Gate` down are different: those run inside
`build.yml` and stop the publish itself.

Signing runs in separate jobs from the build. Only code-owned code runs in a job
that holds the signing key: `build.yml` itself, pinned third-party actions, the
code-owned `install-signing-tools` action, and `ci_tools/sign_image.py` run under
`python3 -I` (standard library only). `build-candidate-image` builds and pushes
the image under a transient tag without the key; `sign-candidate-image`, in the
`production-signing` environment, signs the pushed digest and only then points the
candidate tag at it.

### What each one does not cover

**A green `build.yml` is not a bootable image.** This is the central gap. The
pipeline proves the image composed, carries a matching `kmod-zfs`, is signed by
the committed key, and passed `bootc container lint`. It does not start the image,
load the ZFS module, or touch a pool. A module that builds but misbehaves would
pass every gate here. Rollback to the previous image is the recovery path, and it
stops being unconditionally safe when the ZFS line changes: see
[`safety-model.md`](./safety-model.md).

**`Python Unit Tests` cannot reach the image.** Everything under `tests/` except
`tests/e2e/` mocks every external call, and `tests/e2e/` runs the CLI as a
subprocess but touches no registry, `cosign`, `podman`, or `git`. The
`Containerfile` and `build_files/build-image.sh` execute only inside an image
build, against a real RPM database and module tree. They are two of the three
entries in `.coverage-thresholds.json`'s `unmeasured` section (the third is the
agent gate hook `.claude/hooks/gate-git-diff.sh`, which is bash and which
`tests/test_git_diff_gate.py` does run), and a green suite is not evidence for a
change to either of the two.

The image-side *Python* is a different case and worth separating, because
conflating the two understates what is covered.
`install_zfs_from_akmods_cache.py` and `configure_signing_policy.py` are both
exercised from the host with their external calls mocked, and both carry coverage
floors. What a green suite does not prove about them is the part that was mocked:
the real RPM database, the real module tree, the real `skopeo` output.

**Coverage floors measure the unit tier only.** A module at 100% may never have
had its real-world path exercised, and a module in the 50s may be running against
a live registry every night: at the 2026-10-11 reading,
`ci_tools/write_last_good_build_badge.py` stood at 49% and
`ci_tools/write_akmods_badge.py` at 53%, and both run after every build.
[`CONTRIBUTING.md`](../CONTRIBUTING.md) sets out the three tiers and the priority
order for a real gap.

**There is no coverage instrumentation on the production workflows.** Whether a
path actually runs in production is established by hand today, from a real run's
log. That is a known gap and not a settled design. The per-module floors measure
the unit tier on the host, not what a production run reaches.

### The nightly job answers a question the others cannot

Every gate above runs when something changes, and every signature check among
them runs at *publish* time against an image the same run just built.
[`nightly-compliance.yml`](../.github/workflows/nightly-compliance.yml) asks a
different question on a clock: **does the artifact a user would pull right now
still verify against the committed `cosign.pub`?**

A tag can move, a signature can be pruned, a registry can lose something, none of
which involves a commit, and none of which any other check here would notice. It
runs an hour before `build.yml`'s 06:00 schedule so a failure is visible before
the production build rather than tangled up with it, and it uses the command
[`install-and-verify.md`](./install-and-verify.md) gives users,
`--new-bundle-format=false` included, because verifying differently from the
documented command would test something users are not doing.

It also re-runs the unit suite and the coverage gate. That is deliberate
duplication: a dependency of the *runner* can break those with no commit on this
side, and that failure is worth seeing on its own rather than landing on whoever
opens the next pull request. It reads, it does not repair: it holds
`contents: read` and nothing else.

## The checks that actually protect a booted machine

These are not lint. Each refuses to continue rather than publish something it
could not verify, and each has a message you will see in a job log:

| Refusal | What it prevents |
| --- | --- |
| `SIGNING_SECRET is not configured. Refusing to publish an unsigned production image…` | An unsigned image reaching a machine whose policy requires a signature. |
| `Shared akmods cache … does not provide a kmod-zfs for primary kernel … at ZFS version …` | An image labelled with one ZFS version while shipping another. |
| `Cannot build against kernel version … The maximum supported kernel version is …` (OpenZFS's own `configure`, in the akmods build) | A ZFS module compiled for a kernel it does not support. |
| `Promoted digest mismatch: … produced …, expected …` | `:latest` pointing at something other than what was signed. |
| `Missing required verification key file: …` | Signing or promotion proceeding without the public key that makes the signature checkable. |

When one of these fires, the build is **working**, not broken. The repository's
first rule (AGENTS.md section 0 rule 1) is that the fix is the underlying cause,
never relaxing the check.

## Reading a red build

**First, check which workflow went red.** It changes what the failure means, and
only one of them can publish anything signed:

| Workflow | Runs on | A red run means |
| --- | --- | --- |
| `build.yml` | push to `main`, and 06:00 UTC daily | The production path: **the only one that can have moved `:latest`.** Usually nothing was published, but not always: see the warning above. |
| `build-pr.yml` | every pull request that changes something other than Markdown or `docs/**` | Validation only. Its header says it intentionally stops before any push or signing step, so nothing was published either way. |
| `test.yml` | every pull request and push to `main` | Lint, unit suite, coverage floors. A red run on a pull request stops it merging. |

**Then check that it actually failed.** `gh run list --workflow build.yml --limit
10 --json createdAt,conclusion` distinguishes `failure` from `cancelled`, and the
badge does not. A run of `cancelled` entries with no `failure` among them means
someone was merging quickly, not that anything broke.

Then start with the failing step's stderr line, not the log body. A known refusal
exits 1 with one line, and that line is the diagnosis.
[`.github/prompts/diagnose-build-failure.prompt.md`](../.github/prompts/diagnose-build-failure.prompt.md)
is the procedure, and
[`upstream-change-response.md`](./upstream-change-response.md) has the recovery
steps for each failure.

Three things worth knowing before you conclude anything:

1. **A run of scheduled failures is not one story.** The 21 scheduled failures in
   this repository's history are one rechunk failure, 14 occurrences of OpenZFS
   refusing a 7.1 kernel (13 of them in a row), and six whose logs have expired.
   [`docs/metrics.md`](./metrics.md) has the command that classifies them.
2. **The akmods step's last line is not its cause.** A failed akmods build ends
   `Error: building at STEP "RUN --mount=type=cache,dst=/var/cache/dnf …`
   whatever failed inside it. The reason, such as the kernel ceiling above, is
   earlier in the log.
3. **A green pipeline is not a good image.** A successful run proves the build
   completed. Say which run you read, and what it actually proves.

A third-party service can also turn a run red with nothing wrong here: every
workflow pulls from quay.io and ghcr.io and bootstraps `cosign` from Sigstore.
Check which step failed before concluding anything about signing. A failure in the
`Install skopeo and cosign` step happens before any of this repository's own code
has run.

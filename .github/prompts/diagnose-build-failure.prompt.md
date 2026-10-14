---
description: Work out why a production build is red, with evidence, and say which fix applies
mode: agent
---

# Diagnose a failed build

**Goal:** name the cause with evidence, and say which fix applies.
**Not the goal:** make the build green by any means available.

A red run here is usually a fail-closed check doing exactly its job. The
first row below has no "fix" in this repository at all — the correct outcome
is to stop and report what upstream did.

## 0. Did it fail, or was it cancelled?

```bash
gh run list --workflow build.yml --limit 10 --json createdAt,conclusion,event
```

The build badge shows the most recent *completed* run, and a **cancelled** run
renders as `failing` exactly like a real failure. `build.yml` sets
`cancel-in-progress: true`, so back-to-back merges to `main` each cancel the
run before them.

A column of `cancelled` with no `failure` is a *hypothesis*, not a conclusion.
Check the `event` and the timing before reporting it: `push` runs bunched a few
minutes apart are a merge burst, but `schedule` and `workflow_dispatch` runs can
also be cancelled, and `gh run cancel` means a person stopped the production
workflow on purpose. Reporting "someone was merging quickly" when an operator
cancelled a run deliberately buries the more interesting question.

## 1. Which workflow, and does it publish?

```bash
gh run list --limit 20 --json databaseId,name,event,conclusion,headBranch
```

`Build And Promote Main Image` (`build.yml`) is the only one that signs and
promotes `:latest`. `build-pr.yml` and `build-branch.yml` do not promote, and
`build-branch.yml` publishes unsigned. If the red run is one of those two,
nothing reached `:latest` and the urgency is lower — say so.

## 2. Find the step, not the job

```bash
gh run view <run-id> --log-failed | tail -60
```

Every workflow step that decides anything runs `python3 -m ci_tools.cli
<command>`, and a known refusal exits 1 with one line on stderr. That line is
the diagnosis. Read it before forming a theory.

## 3. Match the message to the guard

These are the fail-closed refusals. Each names a real condition, and the
response to each is different:

| The message says | What actually happened | What to do |
| --- | --- | --- |
| `does not provide a kmod-zfs for primary kernel <k> at ZFS version <v> even after a rebuild` | The akmods cache resolves its own OpenZFS version independently of this repo, and the two disagree. Continuing would label the image `org.zfs-aurora-complex.zfs-version=<v>` while shipping something else. | Upstream problem. Establish whether the ZFS line supports that kernel yet. Do **not** loosen the match. |
| `REQUIRE_MATCH=true needs AKMODS_IMAGE_PINNED` | The post-rebuild verification ran without the digest the "Resolve shared akmods cache digest" step published. Checking the shared tag instead could verify a different image from the one this run signs and builds from. | A wiring problem in `.github/actions/prepare-main-akmods/action.yml`: pass the pin step's `akmods_image_pinned` to the verify step. Do **not** fall back to the tag. |
| `Refusing to check akmods cache ref <ref>: expected <repo>@sha256:<64 hex digits>` | The pinned ref handed to the post-rebuild verification is not a full sha256 digest in this repository's own akmods cache package. | Find out where that ref came from. It should be exactly what `pin-akmods-cache` wrote. Do **not** widen the accepted pattern. |
| `Refusing to pin the akmods cache: AKMODS_BUILT_IMAGE_ID <value> is not the sha256 image ID the build step records.` | The "Resolve shared akmods cache digest" step ran without the local image ID the build step records between `just build` and `just push`, or with something that is not one. The rebuilt cache is already on the shared tag, but nothing has been built from it, signed or promoted. | A wiring problem in `.github/actions/prepare-main-akmods/action.yml`: pass `steps.akmods_build.outputs.built_image_id` to the pin step, and check that `ci_tools/akmods_build_and_publish.py` still writes it. Do **not** let the pin fall back to whatever the shared tag resolves to: the tag can point at an image this run did not build (AGENTS.md section 0 rule 1). |
| `Refusing to pin akmods cache <ref>: its amd64 config is sha256:<a>, but the image this run built is sha256:<b>.` | The digest the shared `main-<fedora>` tag resolved to after this run's push is not the image this run built. Either something else pushed to the shared tag in between (a branch run holds a registry write token and can), or the push rewrote the image config so its digest no longer equals the local image ID. Nothing has been pinned, built from it, signed or promoted. | **Escalate; do not re-run blindly.** Report the digest named and which runs pushed to the akmods package around that time. If no other push happened, compare the local image ID in the build log ("Recorded built akmods image") with the pushed config and report the difference. Never bypass the check or pin from the tag instead: that is exactly how an image this run did not build gets signed (AGENTS.md section 0 rule 1). |
| `Promoted digest mismatch: ... produced <a>, expected <b>` | `skopeo copy` did not preserve the digest. **The copy already happened** — `promote_stable.py` copies, then re-reads the destination — so if the destination named is `:latest`, the tag has already moved to the unexpected digest. | **Check which destination the message names** (the audit tag is copied first, `:latest` second). For an audit tag, stop and investigate. For `:latest`, treat it as live and **escalate immediately**: report what `:latest` currently resolves to, what the last known-good signed digest was, and that machines tracking it will pull whatever is there now. Restoring the tag is a promotion, so it is the maintainer's to perform — propose it, do not do it (AGENTS.md section 0 rule 6). Never bypass the guard itself. |
| `Failed to resolve digest for <ref>` / `Missing digest in skopeo inspect output for <ref>` | A registry read returned a manifest with no digest — usually a transient registry or CDN failure, sometimes a deleted tag. | Check whether the ref still exists. If it does, this is very likely transient (see step 4). |
| `SIGNING_SECRET is empty; cannot sign published image.` | The signing job ran without its secret, typically because the run came from a context that does not get one. | Confirm the trigger. A branch or fork run is not supposed to sign. |
| `Missing required verification key file: <path>` | `cosign.pub` is not where the signing or promotion step expects it. | A repository problem, not upstream. |
| `Replay lock file not found: <path>` | Replay mode was requested with no lock file. | See [`replay-a-build.prompt.md`](replay-a-build.prompt.md). |

## 4. Rule out a third-party service before proposing anything

Every workflow here pulls from quay.io, pushes to ghcr.io, and bootstraps
`cosign` from Sigstore. Any of the three can turn a run red with nothing wrong
in this repository, and all three shapes have been seen:

| Message | What happened |
| --- | --- |
| `happened during read: unexpected EOF` | A quay.io CDN blob transfer died mid-download. |
| `writing blob: … received unexpected HTTP status: 500` | ghcr.io server error on a layer upload. |
| `Error: trusted root is required when using new bundle format` | Sigstore's TUF CDN was unavailable — look for `failed to download https://tuf-repo-cdn.sigstore.dev/…root.json, http status code: 403` just above it. |

**The third one is a trap, twice over.** It names a trusted root and a bundle
format, so it reads like a signing problem here. It is not — it fails while
*installing* `cosign`, not while using it.

But do not then conclude that nothing ran. `install-signing-tools` is invoked in
three separate `build.yml` jobs — `preflight`, `sign-akmods-cache` and
`promote-stable` — and again inside `publish-native-image`, which runs *after*
the transient image has been pushed. A 403 in `preflight`'s copy means almost
nothing happened; the same 403 in `promote-stable`'s means the candidate was
already built, signed and published. **Identify which job's installer failed
before describing the state of the run.**

For any of them: say so, name the job and the step, and note that a re-run is
the maintainer's call — this repo does not retry automatically, and a re-run of
`build.yml` publishes.

## 5. Distinguish "the pipeline is green" from "the image is good"

If you re-check after a re-run, say **which run** you read. A successful run
proves the build completed. It is not evidence the image is safe to boot
(AGENTS.md section 0 rule 4).

## 6. What the answer looks like

State, in this order:

1. Which run and which step, with the link.
2. The guard message, quoted.
3. Whether the run was cancelled rather than failed, or the cause is upstream,
   transient, or in this repository.
4. The fix — or that there is no fix here and what has to change upstream.

If item 3 is anything other than "in this repository", **stop there**. Proposing a code change
to get past a guard that is correctly refusing is the failure mode this
repository cares most about.

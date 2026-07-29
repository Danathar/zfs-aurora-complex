# Proposal: Protect `main` And Isolate The Signing Key

**Status: ADOPTED (2026-07-29).** Both halves are done. Verified live via the GitHub API on
2026-07-29:

| Item | State |
|---|---|
| `production-signing` environment | exists, `custom_branch_policies: true` |
| allowed refs | `branch: main` only |
| admin bypass of protection rules | unticked |
| `SIGNING_SECRET` (repository level) | **deleted** -- repo secret list is empty |
| `SIGNING_SECRET` (environment) | present in `production-signing` |
| `main` ruleset / branch protection | still **none** (see "Deliberately not done") |

Proven by behaviour, not just configuration:

- `main` run `30479538345` built, signed, and promoted with **no repository-level secret in
  existence** -- so the environment copy alone is doing the work. Resulting `:latest`
  `@sha256:38b6f937…` verifies against the committed `cosign.pub`.
- A throwaway branch push (run `30483215044`) built and published
  `br-test-unsigned-branch-publish-44`, and `cosign verify` on that tag **fails** -- the
  branch genuinely could not sign. A verifying signature there would have meant the boundary
  leaked.

The workflow half: `build.yml`'s `build-candidate-image` and `sign-akmods-cache` declare
`environment: production-signing`; `build-branch.yml` references `SIGNING_SECRET` nowhere at
all; branch runs cannot rebuild the shared akmods cache (`allow_cache_rebuild: "false"`,
stricter than the options below, chosen after a branch run swapped the shared tag mid-flight);
and branch testing uses explicitly-unsigned `br-*` tags ("What breaks" option 1, chosen by the
maintainer -- rules in [`install-and-verify.md`](./install-and-verify.md)).

### Deliberately not done

**No `main` ruleset or branch protection.** This was proposed in section 4 below and the
maintainer chose not to apply it, on reasoning established during the review: for a
single-collaborator repository, branch protection does not stop the threat it appears to. A
leaked write-scoped token can open a pull request *and merge it*, and requiring approvals is
not usable for a solo maintainer (GitHub blocks self-approval). Meanwhile nothing in the
repository defends against full compromise of the owner account, because every control here is
administered by that account. Hardware 2FA on the GitHub account is worth more than this rule.

Section 4 is retained below unchanged as the record of what was proposed and why, including
the "Lockout safety" analysis, should this be revisited.

**Migration gotchas that actually bit** -- worth reading before re-doing this anywhere:

1. The environment secret was first added by pasting the key into the web UI, which mangled
   it. `cosign` then failed at signing time with
   `reading key: decrypt: invalid character ';' after object key` -- *after* the secret
   resolved and the registry login succeeded, so it presented as a permissions problem when it
   was a content problem. Set it from the file:
   `gh secret set SIGNING_SECRET --env production-signing < cosign.key`.
2. GitHub's documentation used different labels than the product. See section 1.

## The problem, in two parts that must be fixed together

*Historical record of the situation this document was written to fix. Both parts are now
resolved for the secret (see Status above); the branch-protection part was deliberately
declined.* Verified live against this repository on 2026-07-27:

- `main` has no branch protection and no repository ruleset. A direct push to `main` triggers
  build, sign, and promote to `:latest` with nothing in between.
- `SIGNING_SECRET` is a plain **repository-level** secret. Every workflow run in this repo can
  read it, including `build-branch.yml` runs triggered by a push to any branch.

Read separately, each looks like its own fix (protect `main`; move the secret). Read together,
fixing only one is close to meaningless:

- If you protect `main` but leave `SIGNING_SECRET` at the repository level, a branch push can
  still read the production signing key. Someone (or something: a compromised token, a
  compromised collaborator account, a supply-chain-compromised bot integration with write
  access) that can push a branch can edit `build-branch.yml` on that branch to do anything with
  the key it wants -- exfiltrate it, sign something outside this pipeline's normal flow -- and
  that branch push never had to touch `main` at all.
- If you move the secret but leave `main` unprotected, a direct push to `main` still triggers a
  full build-sign-promote cycle with no review gate.

**Both changes are proposed together for that reason.** Doing one without the other narrows the
attack surface without closing it.

### Precision on the threat model

"Anyone who can push a branch" means anyone with **write access to this repository** -- not the
general public. This is not about external fork contributors: `build-branch.yml` triggers on
`push`, and GitHub only runs `push`-triggered workflows for pushes from someone with write
access, not from a fork. The realistic threats this closes are a compromised personal access
token, a compromised collaborator account, or a compromised bot/automation integration that has
write access (Renovate already has it, for example) -- not a random GitHub user opening a PR.

### A note on cache signing (PR #44, now merged)

The `sign-akmods-cache` / `sign-branch-akmods-cache` jobs pass `SIGNING_SECRET` to branch-push
workflows too, for a real reason (an unsigned branch-rebuilt cache is a cache nothing can ever
trust on reuse). This is not new in kind -- `build-branch.yml`'s existing "Push and sign branch
image" step already did the same for human-authored branches -- but it is a second instance of
it. This proposal accounts for both; see "What breaks" below.

This widened rather than narrowed the exposure described above, and it was live for two days: a
branch run signed the shared production cache on 2026-07-28. **Closed since** -- #51 and then
#57 removed branch signing entirely and barred branch runs from rebuilding the cache. Kept here
because any image published in that window carries the provenance caveat.

## Proposed changes

*Everything from here down is the original proposal, preserved as the record of what was
considered and why. It is accurate to 2026-07-27 and describes jobs and behaviour that have
since changed -- notably `sign-branch-akmods-cache`, which no longer exists (#57 deleted it),
and branch cache rebuilding, which is now forbidden outright. Read the Status section at the
top for what is actually true now.*

### 1. A GitHub Environment scoped to `main`, holding the signing key

Create an environment (suggested name: `production-signing`) with a deployment branch
restriction limiting it to `main` only. Exact UI path, verified against the live interface
on 2026-07-29 (GitHub's own documentation used different labels than the product did, so trust
this): Settings -> Environments -> **New environment** -> name it -> **Configure environment**
-> the **"Deployment branches and tags"** section, whose dropdown defaults to **"No
restriction"** -> choose **"Selected branches and tags"** -> add a rule with ref type **Branch**
and pattern `main`.

While on that screen, note **"Allow administrators to bypass configured protection rules"** is
ticked by default. It does not affect the deployment-branch restriction on secrets, but it is
the environment-level twin of the ruleset admin-exemption question in section 4 -- untick it
for the same reason, since in a solo-maintainer repository the owner's credential is the one
worth protecting against.

Create it *before* merging the workflow change. Referencing an environment that does not exist
auto-creates it, but with no protection rules at all -- so merging first briefly yields an
unprotected environment rather than a restricted one.

Move `SIGNING_SECRET` into that environment as an environment secret, and remove the
repository-level copy once the migration below is verified working.

**Set the secret from the key file, never by pasting.** A paste can lose or rewrite newlines,
and a cosign private key is JSON -- a mangled copy fails at signing time with
`reading key: decrypt: invalid character ';' after object key`, well after the secret has
resolved and the registry login has succeeded, which makes it look like a permissions problem
when it is a content problem. This happened during the real migration on 2026-07-29. Use:

```bash
gh secret set SIGNING_SECRET --env production-signing < /path/to/cosign.key
```

Confirm the file is the right key first -- the public half it derives must equal the committed
`cosign.pub`, or every already-signed image stops verifying:

```bash
COSIGN_PASSWORD="" cosign public-key --key /path/to/cosign.key | diff - cosign.pub && echo MATCH
```

This is the mechanism that actually closes the exfiltration path: GitHub enforces the branch
restriction at the point a job requests the secret, based on the ref the job is actually running
against -- **not** based on what the workflow file says. Even if someone edits a workflow file on
their own branch to add `environment: production-signing` and try to read the secret, GitHub
refuses to expose it, because that job is not running against an allowed ref. Editing the
workflow file cannot forge the ref a job runs on.

### 2. Jobs that only ever run from `main` adopt the environment

`build.yml` triggers only on `schedule`, `push: branches: [main]`, and `workflow_dispatch`
(which itself runs against whichever branch is selected when dispatched -- normally `main`).
Every job in that file that references `secrets.SIGNING_SECRET` today can add
`environment: production-signing` with no behavior change for the common case:

- `build-candidate-image` (signs the candidate OS image)
- `sign-akmods-cache` (added in PR #44)

If a manual `workflow_dispatch` is ever run against a non-`main` branch, these jobs would then
correctly lose access to the secret -- that is the restriction working as intended, not a bug.

### 3. Jobs that run from branch refs lose signing access -- by design

`build-branch.yml` runs from whatever branch was pushed, which is a ref this environment
explicitly does not allow. Once the secret moves, these jobs described below **cannot** use
`secrets.SIGNING_SECRET` at all, environment-scoped or otherwise:

- `build-branch.yml`'s "Push and sign branch image" step (existing, predates this proposal)
- `sign-branch-akmods-cache` (added in PR #44)

This is the part of the task most directly worth a real decision, not a rubber stamp, because it
removes real capability that exists today.

## What breaks, and the options for each

### Branch OS-image testing (`build-branch.yml` "Push and sign branch image")

Today, a human-authored branch push builds, pushes, and **signs** a test image so it can be
tried on real hardware via `bootc switch --enforce-container-sigpolicy`. Once branch pushes lose
signing access, that step can still **push an unsigned image**, but the in-image trust policy
(`docs/signing-and-bootc.md`) requires a valid cosign signature matching the committed
`cosign.pub` for `--enforce-container-sigpolicy` to succeed. An unsigned branch image cannot be
switched to under enforced policy.

Options, not mutually exclusive:

1. **Accept the loss, but this needs an actual workflow change, not just removing the
   secret.** `publish-native-image`'s "Require signing key before publication" step
   (`.github/actions/publish-native-image/action.yml:34-39`) exits before any push when the key
   is empty, so simply cutting branch access to `SIGNING_SECRET` does not degrade to "push
   unsigned" -- it makes `build-branch.yml`'s "Push and sign branch image" step fail outright,
   with no branch image published at all. Getting back to a pushed-but-unsigned test image would
   require changing that step to add an explicit unsigned-publish path for branches. Testing that
   image with plain `bootc switch` (no `--enforce-container-sigpolicy`) would also be a *new*,
   weaker posture, not the existing one: `README.md:44-55` and `docs/signing-and-bootc.md:20-25`
   currently require `--enforce-container-sigpolicy` on every first switch, and
   `README.md:268-269` tells anyone who switched without it to switch again with it. Adopting this
   option means updating those docs to describe a deliberately weaker branch-testing path, not
   just skipping a flag.
2. **A separate, lower-privilege test-signing key.** A second cosign keypair, scoped to an
   environment that *does* allow branch refs, used only for `br-*` tags. The production
   `cosign.pub` embedded in real images would not trust it, so a compromised branch push could
   forge a *test*-trusted image but never a production-trusted one. More moving parts (a second
   key to generate, store, and rotate) for a benefit that only matters if branch images are
   regularly tested on real hardware under enforced policy.
3. **Stop pushing/signing branch OS images entirely**, keeping only the local
   build-and-rechunk validation that already runs for every branch (which proves the branch
   composes, just not on real hardware). Simplest, but removes the "try this branch on a test
   VM" workflow this repo's own docs describe.

This document does not choose one. It is the owner's call, and depends on how often branch
images actually get tried on real hardware versus validated as "does this still build."

### Branch akmods-cache signing (`sign-branch-akmods-cache`, PR #44)

A branch rebuild of the shared cache could no longer be signed once the secret moves. Per PR
#44's own logic, an unsigned cache is treated as unusable on reuse -- so a branch-rebuilt cache
would need to be rebuilt again by the *next* consumer, including the next `main` run, with no
caching benefit carried forward from the branch. This is a real cost (slower branch validation,
and `main` pays a full rebuild the first time it needs a kernel/ZFS combination a branch already
built) but it is safe: nothing trusts an unsigned cache, so nothing regresses in correctness,
only in cache-warming convenience.

No action needed beyond accepting this cost, unless the owner wants to revisit PR #44's
"branch-scoped cache tag instead of the shared one" alternative (raised, not implemented, in
that PR) at the same time -- that would sidestep this cost entirely by giving branches their own
cache namespace instead of contending for the shared one.

### `build-pr.yml`

No change. That workflow never held `SIGNING_SECRET` and does not sign anything today.

## 4. Branch protection on `main`

Recommend a repository ruleset (the current GitHub mechanism; the classic "branch protection
rules" API is being superseded by this) targeting `main`, requiring:

- a pull request before merging
- this status check to pass first: **Python Unit Tests** (`test.yml`; confirmed via its `on:`
  trigger to run on every PR unconditionally, with no path filters)
- no force pushes

**Do not also require `Build PR Image (No Push)`**, even though it runs on most PRs today: read
the current `build-pr.yml` and its `paths-ignore` excludes `README.md`, `docs/**`, and all
Markdown files. A docs-only PR -- including this one -- never triggers that workflow, so it never
produces that check, and a *required* check that never runs blocks the PR from merging forever.
If the owner wants image-build validation to gate every merge, including documentation changes,
`build-pr.yml`'s path filters would need to be removed first as a separate change, before adding
it to the ruleset.

**Do not require PR approvals (reviewer count) beyond zero.** This is a solo-maintainer
repository; GitHub blocks self-approval by default, so requiring even one approval would either
lock the owner out of merging their own PRs or require configuring an approval exception that
adds complexity for no real benefit here. The status-check requirement is what actually matters:
it stops an accidental or malicious direct push from skipping CI, which a required-reviewer
count does not add much to in a one-maintainer repo.

**Do enable "include administrators".** This reverses an earlier recommendation in this
document, which said to leave administrators exempt so a hotfix is never blocked. That advice
was wrong for this repository's actual threat model. The threat here is not the owner making a
hurried mistake, it is a **compromised credential** -- and in a solo-maintainer repo the owner's
own credential is the single most valuable one to steal. An admin exemption means the rule
protects against everything except the case worth protecting against. The hotfix concern is real
but cheap to handle: a ruleset can be disabled from Settings in seconds (see "Rollback"), which
is a deliberate, logged act rather than a standing hole.

## 5. The privileged build-container path (closed in PR #48, recorded here)

An external audit found a path this document originally missed, and it is worth recording
because it shows the limit of what an environment restriction can do.

`build.yml` and `build-branch.yml` accepted a free-text `workflow_dispatch` input naming the
image for the akmods job -- a job running `--privileged`, as root, with `/` bind-mounted, and a
package-write token. Anyone able to dispatch could run arbitrary code there **against the real
`main` ref**, publish a cache that later trusted jobs sign and build from, and reach `:latest`.
Because `install_zfs_from_akmods_cache.py` installs every RPM in the cache that is not
`.src`/`-debug`/`-devel`/`-test`, with no package-name allowlist, that meant arbitrary package
installation into the published image.

**A `main`-only environment would not have closed this**, because a dispatch against `main`
satisfies the branch restriction. Nor could a validation step: a job's `container:` is resolved
and started before any step runs, so a guard would execute inside the container it was meant to
gate. PR #48 removed the input entirely; the build container is now only changeable by a
reviewed edit to `ci/defaults.json` plus the workflow literals, with a test enforcing that they
stay identical.

The lesson for the rest of this proposal: **branch-scoping the secret is necessary but not
sufficient.** Anything that lets a dispatcher choose what *code runs* in a trusted position
bypasses it. Worth re-checking any future workflow input against that question.

## 6. Repository security settings (separate from the above, all owner-applied)

Verified disabled on 2026-07-28:

| Setting | State | Suggested |
|---|---|---|
| Dependabot alerts | disabled (`/dependabot/alerts` → 403) | enable |
| Dependabot security updates | disabled | enable |
| Automated security fixes | `false` | enable |
| Code scanning | no analysis | consider; see caveat |
| Actions policy | `allowed_actions: all` | consider restricting |
| Secret scanning + push protection | **enabled**, 0 alerts | keep |

Notes:

- **Dependabot alerts are worth enabling even though Renovate handles updates.** They are
  different things: Renovate opens version-bump PRs, alerts tell you a dependency has a
  published advisory. Enabling alerts does not conflict with Renovate and does not create
  competing PRs unless security *updates* are also enabled.
- **Code scanning is a judgement call, not an obvious win.** This is a small Python CI-tooling
  repo with no network-facing service; CodeQL's Python rules would mostly find nothing, and a
  new workflow has real maintenance cost. Worth it mainly if you want the GitHub Security tab
  populated. Not recommended as urgent.
- **Restricting `allowed_actions`** to "selected actions" would let you require SHA pinning
  repository-wide. This repo already SHA-pins everything by convention, so the setting mostly
  guards against a future lapse. Low cost, low urgency.
- No SBOM, container vulnerability scan, or provenance attestation exists. Of those, a
  vulnerability scan of the published image is the one with a real argument behind it, since
  this image is a daily driver -- but it is a new workflow with ongoing noise, so it belongs in
  a deliberate decision rather than being bundled here.

## Rollback

Both changes are reversible without any data loss:

- Repository rulesets can be disabled or deleted instantly from Settings -> Rules -> Rulesets,
  restoring unrestricted push access to `main` immediately.
- An environment secret can be copied back to a repository-level secret at any time (re-add
  `SIGNING_SECRET` at the repository level); the environment can be left in place unused or
  deleted. Removing the branch restriction on the environment (or deleting the environment) does
  not affect the key material itself -- it is the same cosign keypair throughout.

Nothing here requires re-keying, re-signing existing images, or touching `cosign.pub`/`cosign.key`.

## Suggested order of operations, to avoid a self-inflicted lockout

1. Create the `production-signing` environment with the `main`-only branch restriction. Add
   `SIGNING_SECRET` to it. **Do not remove the repository-level secret yet.**
2. In a PR, update the jobs listed in "Jobs that only ever run from `main`" above to add
   `environment: production-signing`. Both the environment-scoped and repository-level secret
   exist simultaneously at this point, so this PR cannot break anything -- the environment-scoped
   reference will resolve to the same secret value either way.
3. Merge that PR, then trigger one real `main` build (push or `workflow_dispatch`) and confirm
   signing still succeeds, now sourced from the environment.
4. Only after that real run succeeds: delete the repository-level `SIGNING_SECRET`. Confirm the
   branch-signing steps identified above now correctly fail closed (no secret available) rather
   than silently signing with something unexpected.
5. Decide on one of the "what breaks" options above for branch OS-image testing, and implement
   it (or explicitly accept the loss and document it in `README.md`'s Safety Model section).
6. Only after 1-5 are settled and working: add the `main` ruleset. Protecting `main` first, before
   the key is actually isolated, protects nothing that matters yet -- the branch bypass would
   still be wide open.

## Lockout safety

The maintainer's stated priority is not getting locked out of their own repository. Nothing
proposed here can do that permanently, but two things can *stall* work and one would be a hard
stop. Worth reading before touching any setting.

### The escape hatch

A ruleset can be set to **Disabled** without deleting it (Settings -> Rules -> Rulesets). Every
ruleset rule type governs refs and content -- pushes, deletions, required checks, linear history,
signatures -- and none of them govern who may administer repository settings. So a ruleset cannot
revoke the ability to turn that ruleset off.

*Caveat, stated plainly:* GitHub's ruleset documentation describes the disable/delete mechanics
but does not spell out the permission model, so that last sentence is inference from the rule
taxonomy rather than a quoted guarantee. Confirm it once by creating a trivial ruleset and
checking that Settings -> Rules is still reachable, before relying on it under pressure.

### The three real failure modes

1. **A required status check that never runs. This is the only true lockout.** A PR that cannot
   produce a required check can never merge. This is not hypothetical: requiring
   `Build PR Image (No Push)` would have made every documentation-only PR permanently
   unmergeable, because `build-pr.yml` skips `docs/**` and `**/*.md`. That is why section 4
   requires only **Python Unit Tests**. Residual risk: if `test.yml`'s job name ever changes,
   the required check stops appearing and every PR stalls. Symptom to recognise -- PRs blocked
   on a check that is not merely pending but absent. Fix by disabling the ruleset.
2. **"Prevent self-review" on an environment. Do not enable it.** It is an optional setting and
   is off by default. Left off, the maintainer can approve their own runs, which is what makes
   the required-reviewer gate in `runtime-validation-proposal.md` usable at all. Enabled in a
   solo-maintainer repo it is a hard stop: the only reviewer is forbidden from reviewing.
3. **Deleting the repository-level `SIGNING_SECRET` too early.** This breaks signing, not
   access. The order of operations below keeps both copies until a real run proves the
   environment-scoped one works.

### On "include administrators"

Section 4 recommends enabling it. That does **not** create a lockout, because the escape hatch
above is a settings change, not a push. What it costs is those extra clicks during a hotfix.

The alternative, if even that is unwanted, is adding the owner as a **bypass actor**: pull
requests stay required by default and bypasses are recorded, but the owner's credential can push
directly. That is strictly weaker against the compromised-credential case, which is the entire
reason for the rule. It is a real trade, not a free option -- choose it deliberately if at all.

### Rollout order that keeps every step reversible

Verify each step before adding the next, so a problem is always attributable to the last change:

1. Create the ruleset **without** required status checks. Confirm a trivial PR still merges.
2. Add **Python Unit Tests** as a required check. Open a **docs-only** PR and confirm it still
   merges -- this is the exact case that would break under a wrongly chosen required check.
3. Create the environment. **Leave "Prevent self-review" unchecked.**
4. Keep both `SIGNING_SECRET` copies until a real `main` run signs successfully.
5. Add required reviewers last, once everything above is proven.

## Why this is a proposal, not a PR that changes settings

Repository rulesets, branch protection, environments, and secret placement are GitHub repository
*settings*, not files this repo's Git history tracks. An agent PR cannot create or modify them --
only someone with admin access to the repository, acting directly in GitHub's UI or via
`gh api`/`gh ruleset`, can. This document is meant to be reviewed (as a normal PR, since it is a
tracked file) and then acted on by the owner outside of any PR, following the order of
operations above.

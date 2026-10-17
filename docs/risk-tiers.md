# Risk tiers

How much scrutiny a change needs here, and why the answer is not "how many
lines did it touch".

The usual proxies do not work in this repository. A one-line diff in
`promote_stable.py` can move `:latest` to something unsigned; a thousand-line
documentation rewrite cannot reach a booted machine at all. So tiers are
assigned by **what the change can cause**, not by size, and the `area/*` labels
that [`.github/labeler.yml`](../.github/labeler.yml) attaches are a first
approximation of that.

This repository is in production: the maintainer daily-drives the image it
publishes, with multi-terabyte ZFS pools attached, and `build.yml` publishes
every night as well as on every merge to `main`. The tiers describe how close a
change sits to that machine.

[`review-rubric.md`](./review-rubric.md) is what a reviewer does. This page is
how much of it to do.

## The tiers

### Tier 3 — can reach a booted machine

**Anything touching the seven files in `AGENTS.md` section 0 rule 2** (which
`CLAUDE.md` section 0 repeats), plus the ZFS line, anything pool-facing, the files the image installs
onto every booted machine, every workflow file, the policy file that says what
each workflow's token may do, the ruleset that keeps `main` behind a pull
request, and the files that decide what an agent may run here without a prompt.
Labelled `area/safety-critical`.

- `.github/workflows/build.yml`
- `.github/actions/publish-native-image/`
- `ci_tools/sign_image.py`
- `ci_tools/promote_stable.py`
- `ci_tools/check_akmods_cache.py`
- `containerfiles/zfs-akmods/install_zfs_from_akmods_cache.py`
- `files/scripts/configure_signing_policy.py`
- `files/`, because the image build copies what is under it onto every booted
  machine: today the ZFS module autoload (`usr/lib/modules-load.d/zfs.conf`),
  a tmpfiles.d entry (`usr/lib/tmpfiles.d/zfs-aurora-complex.conf`) and the
  signing-policy script above
- `cosign.pub`, `ci/defaults.json`'s ZFS and base-image values
  (`DEFAULT_ZFS_MINOR_VERSION`, `DEFAULT_BASE_IMAGE`, `STABLE_SIGNAL_IMAGE`,
  `DEFAULT_BUILD_CONTAINER_IMAGE`)
- `.github/workflows/`, every workflow and not only `build.yml`, because once a
  workflow is on `main` any job in it can declare the production-signing
  environment and be handed the key that signs the published image. The cost:
  every workflow change waits for the owner, Renovate's pin bumps included
- `.github/policies/workflow-permissions.json`, because a token that gains
  `packages: write` can push an image to the registry these machines pull from
- `.github/rulesets/`, because the ruleset in it is what keeps `main`, and so
  the signing build, behind a pull request. Loosening it reopens the direct
  push; see [`branch-protection.md`](./branch-protection.md)
- `.github/CODEOWNERS`, because it lists the paths in this tier and the ruleset
  requires the code owner's approval for them. A line dropped from it takes
  that path out from behind the owner
- `.claude/settings.json`, `.claude/hooks/`, `.claude/commands/`,
  `tests/run_tests.py`: together these are the agent permission boundary. The
  settings file is the permission table: its deny list keeps signing, registry
  pushes, merges and workflow dispatch away from an agent, its allow list is
  what runs with no prompt, and its hooks block is what registers
  `.claude/hooks/gate-git-diff.sh` at all. The gate keeps the allow-listed git,
  gh and cosign commands from reading or writing past those rules, and the
  runner keeps the one allow-listed test command from importing code that is
  not in the diff. A command file looks like documentation, but its frontmatter
  can grant tools for as long as the command runs. By path alone each of these
  was Tier 1, which an agent may change unattended, and a change that drops one
  refusal hands every later session a denied operation with no prompt in front
  of it; see [SECURITY-AI.md](./SECURITY-AI.md)

**What it requires:** the full rubric. The safety-critical statement is
mandatory and is the review, not a formality. If the ZFS line or anything
pool-facing moves, rollback must be addressed explicitly — an image that
activates newer pool features can leave the previous image unable to import
those pools, which breaks the recovery path everything else assumes
([`safety-model.md`](./safety-model.md)).

**Never in this tier, regardless of who asks:** weakening a fail-closed check.
That is not a tier, it is a refusal (`AGENTS.md` section 0 rule 1).

**An agent may not complete a Tier 3 change unattended.** It may propose one,
with the analysis, on a branch. GitHub enforces this: every path above is in
[`.github/CODEOWNERS`](../.github/CODEOWNERS), and the ruleset requires the
code owner's approving review before a pull request that touches one can merge
([`branch-protection.md`](./branch-protection.md#the-ruleset)).

### Tier 2 — can break the build or a published artifact indirectly

The rest of the image and the decision logic: `Containerfile`,
`build_files/`, `containerfiles/`, the other `ci_tools/` and `shared/`
modules, `ci/` (the parts of `defaults.json` Tier 3 does not name, and
`inputs.lock.json`), and `renovate.json`. Labelled `area/build`, `area/ci`,
`area/ci-tools`; the workflow files the CI label also matches are Tier 3.

**What it requires:** rubric sections 1, 4, 5 and 6. A green unit suite is not
sufficient evidence — `Containerfile` and `build_files/` execute only inside an
image build, so say how the change was verified or say plainly that it was not.

Watch specifically for a new external action in a composite action under
`.github/actions/`. A widened `permissions:` block, a new secret reference, a
new `environment:` or a new external action in a workflow is a Tier 3 question,
and since every workflow file is Tier 3 it waits for the owner's approval.
Once `.github/policies/workflow-permissions.json` is in the tree, a widened
block cannot arrive alone: the unit suite fails until that file changes in the
same pull request, and that file is Tier 3.

### Tier 1 — can only break CI or mislead a reader

`tests/`, `.coverage-thresholds.json`, docs, and agent instructions. Labelled
`area/tests`, `area/docs`, `area/agent-config`.

**What it requires:** rubric sections 5 and 7. Two specific things still bite at
this tier:

- **A test that does not test.** `assertRaises(CiToolError)` alone passes for
  any reason at all, including an unrelated missing environment variable.
- **Doc drift.** This repository has shipped prose that contradicted the code.
  A doc change that makes an existing sentence false is a defect, not a nit.

A lowered coverage floor is Tier 1 by path and Tier 2 by meaning — it is a claim
that a code path went away. Say which.

### Tier 0 — cannot affect anything that runs

Nothing here reaches CI or the image: `README.md` badges, `LICENSE`,
`.editorconfig`, `.gitignore`.

Worth knowing because `build.yml`, `build-pr.yml` and `build-branch.yml` all set
`paths-ignore` for `**/*.md` and `docs/**`, so a documentation-only change
**starts no build at all**. Any other file, including a JSON policy or a
workflow that is not the build, starts a production `build.yml` run on merge:
it rebuilds from current inputs, as the daily cron does, and promotes the result
if every gate passes.

## Assigning a tier when the labels disagree

The labels are path-based and a change can carry several. **Take the highest
tier any label implies**, and note that `area/*` labels are descriptive only:
they never mean approval, which is the whole reason they live in their own
namespace ([`SECURITY-AI.md`](./SECURITY-AI.md), "Labels carry authority").

Two cases the paths get wrong on their own:

| Looks like | Actually |
| --- | --- |
| A test-only diff that deletes a test exercising a fail-closed guard | Tier 2. The guard stops being covered, and the floor in `.coverage-thresholds.json` is the only thing that notices. |
| A docs-only diff that edits `AGENTS.md` or `CLAUDE.md` | Tier 2. It changes what future agent sessions are told to do. What they are permitted to do is `.claude/settings.json` and the files Tier 3 lists beside it. |

When two readings are defensible, take the higher one and say why in the pull
request. The cost of over-reviewing a change here is minutes; the cost of
under-reviewing one is an image on somebody's machine.

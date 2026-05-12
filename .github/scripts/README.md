# Workflow Command Map

The workflows in this repo call Python through one shared command-line interface (CLI) entrypoint:

- `python3 -m ci_tools.cli <command>`

That CLI dispatches to the real implementation in [`ci_tools/`](../../ci_tools).
This keeps the workflow files focused on job order, permissions, and data flow.

If a term is unfamiliar, check the shared glossary first:
[`docs/glossary.md`](../../docs/glossary.md)

## CLI Command Map

| Workflow step (example) | CLI command | Python module |
|---|---|---|
| Resolve build inputs | `resolve-build-inputs` | `ci_tools.resolve_build_inputs` |
| Write build inputs manifest | `write-build-inputs-manifest` | `ci_tools.write_build_inputs_manifest` |
| Check shared akmods cache | `check-akmods-cache` | `ci_tools.check_akmods_cache` |
| Export normalized registry context for later workflow steps | `export-registry-context` | `ci_tools.tagging_context` |
| Export checked-in repo defaults for workflow steps | `export-repo-defaults` | `ci_tools.export_repo_defaults` |
| Resolve pull request (PR) validation inputs and verify shared akmods cache | `prepare-validation-build` | `ci_tools.prepare_validation_build` |
| Compute branch-safe image tag prefix | `compute-branch-metadata` | `ci_tools.tagging_context` |
| Compose final branch image tag | `compose-branch-image-tag` | `ci_tools.tagging_context` |
| Compute candidate image tag | `compute-candidate-tag` | `ci_tools.tagging_context` |
| Promote candidate digest to latest and audit tags | `promote-stable` | `ci_tools.promote_stable` |
| Sign one published image tag by digest | `sign-image` | `ci_tools.sign_image` |
| Clone resolved upstream akmods tooling and verify the exact commit SHA | `akmods-clone-pinned` | `ci_tools.akmods_clone_pinned` |
| Configure target image path for the akmods build wrapper | `akmods-configure-zfs-target` | `ci_tools.akmods_configure_zfs_target` |
| Build and publish shared self-hosted ZFS akmods image | `akmods-build-and-publish` | `ci_tools.akmods_build_and_publish` |
| Classify an akmods build failure for sticky-issue triage | `classify-akmods-failure` | `ci_tools.classify_akmods_failure` |

### Akmods Failure Classification

`classify-akmods-failure` reads `artifacts/akmods-build.log` after the shared
ZFS akmods build fails. It writes `artifacts/akmods-failure.json` for the
sticky-issue workflow and appends a short GitHub step summary when
`GITHUB_STEP_SUMMARY` is available.

The classifier is intentionally small and fail-closed:

- `upstream-compat` means the log matched a known ZFS/kernel compatibility
  surface. The build still fails, and image promotion remains blocked.
- `unknown` means the log did not match a known compatibility pattern and needs
  manual investigation.

For OpenZFS configure output, the classifier also compares `ZFS_META_KVER_MAX`
with the resolved `KERNEL_RELEASE`. If the base image kernel is newer than the
OpenZFS-declared maximum, the payload explains that the build is intentionally
failing closed until OpenZFS supports that kernel line.

## Workflow Map

- [`build.yml`](../workflows/build.yml)
  - main candidate build, promotion, and signing
- [`build-branch.yml`](../workflows/build-branch.yml)
  - branch-tagged push using shared-cache reuse or rebuild when required
  - bot-authored runs stop after local validation and do not push/sign public branch tags
- [`build-pr.yml`](../workflows/build-pr.yml)
  - no-push validation build
- [`akmods-failure-triage.yml`](../workflows/akmods-failure-triage.yml)
  - runs after `build.yml` completes via `workflow_run` trigger
  - opens or updates a sticky GitHub issue on classified akmods failures
  - auto-closes sticky issues when the next build succeeds

## Local Workflow Actions

These composite actions keep the workflow files focused on job order and data flow:

- [`load-ci-defaults`](../actions/load-ci-defaults/action.yml)
  - exports values from `ci/defaults.json`
- [`prepare-main-akmods`](../actions/prepare-main-akmods/action.yml)
  - resolves main-workflow inputs, uploads the build-input manifest, verifies shared akmods cache state, and rebuilds the shared cache only when required
- [`prepare-registry-context`](../actions/prepare-registry-context/action.yml)
  - computes lowercase GitHub Container Registry (GHCR) paths and whether the current account is an automation bot
- [`build-native-image`](../actions/build-native-image/action.yml)
  - wraps the standard buildah invocation and build arguments for this repo
- [`install-signing-tools`](../actions/install-signing-tools/action.yml)
  - installs `skopeo` and `cosign`
- [`publish-native-image`](../actions/publish-native-image/action.yml)
  - requires `SIGNING_SECRET`, logs in to GHCR, pushes one tag, and signs the pushed digest

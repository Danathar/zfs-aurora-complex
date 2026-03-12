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
| Export checked-in repo defaults for workflow steps | `export-repo-defaults` | `ci_tools.export_repo_defaults` |
| Publish or repair the shared-cache metadata tag | `publish-akmods-cache-metadata` | `ci_tools.publish_akmods_cache_metadata` |
| Resolve pull request (PR) and branch validation inputs and verify shared akmods cache | `prepare-validation-build` | `ci_tools.prepare_validation_build` |
| Compute branch-safe image tag prefix | `compute-branch-metadata` | `ci_tools.compute_branch_metadata` |
| Promote candidate digest to latest and audit tags | `promote-stable` | `ci_tools.promote_stable` |
| Sign one published image tag by digest | `sign-image` | `ci_tools.sign_image` |
Note: branch workflows skip this step when `SIGNING_SECRET` is unavailable, which is expected for some automation actors such as Dependabot.
| Clone pinned upstream akmods tooling and verify the exact commit SHA | `akmods-clone-pinned` | `ci_tools.akmods_clone_pinned` |
| Configure target image path for the akmods build wrapper | `akmods-configure-zfs-target` | `ci_tools.akmods_configure_zfs_target` |
| Build and publish shared self-hosted ZFS akmods image | `akmods-build-and-publish` | `ci_tools.akmods_build_and_publish` |

## Workflow Map

- [`build.yml`](../workflows/build.yml)
  - main candidate build, promotion, and signing
- [`build-branch.yml`](../workflows/build-branch.yml)
  - branch-tagged push using read-only shared-cache validation
  - bot-authored runs stop after local validation and do not push/sign public branch tags
- [`build-pr.yml`](../workflows/build-pr.yml)
  - no-push validation build

## Local Workflow Actions

These composite actions keep the workflow files focused on job order and data flow:

- [`load-ci-defaults`](../actions/load-ci-defaults/action.yml)
  - exports values from `ci/defaults.json`
- [`prepare-registry-context`](../actions/prepare-registry-context/action.yml)
  - computes lowercase GitHub Container Registry (GHCR) paths and whether the current account is an automation bot
- [`build-native-image`](../actions/build-native-image/action.yml)
  - wraps the standard buildah invocation and build arguments for this repo
- [`install-signing-tools`](../actions/install-signing-tools/action.yml)
  - installs `skopeo` and `cosign`
- [`publish-native-image`](../actions/publish-native-image/action.yml)
  - logs in to GHCR, pushes one tag, and signs it when a private key is available

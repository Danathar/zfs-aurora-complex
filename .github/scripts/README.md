# Workflow Command Map

Workflows in this repo call Python through one shared CLI entrypoint:

- `python3 -m ci_tools.cli <command>`

That CLI dispatches to the real implementation in [`ci_tools/`](../../ci_tools).
This keeps workflow YAML focused on job order, permissions, and data flow.

If a term is unfamiliar, check the shared glossary first:
[`docs/glossary.md`](../../docs/glossary.md)

## CLI Command Map

| Workflow step (example) | CLI command | Python module |
|---|---|---|
| Resolve build inputs | `resolve-build-inputs` | `ci_tools.resolve_build_inputs` |
| Write build inputs manifest | `write-build-inputs-manifest` | `ci_tools.write_build_inputs_manifest` |
| Check shared akmods cache | `check-akmods-cache` | `ci_tools.check_akmods_cache` |
| Resolve PR/branch validation inputs and verify shared akmods cache | `prepare-validation-build` | `ci_tools.prepare_validation_build` |
| Compute branch-safe image tag prefix | `compute-branch-metadata` | `ci_tools.compute_branch_metadata` |
| Promote candidate digest to latest and audit tags | `promote-stable` | `ci_tools.promote_stable` |
| Sign one published image tag by digest | `sign-image` | `ci_tools.sign_image` |
| Clone pinned upstream akmods tooling and patch publish-name behavior | `akmods-clone-pinned` | `ci_tools.akmods_clone_pinned` |
| Configure target image path for the akmods build wrapper | `akmods-configure-zfs-target` | `ci_tools.akmods_configure_zfs_target` |
| Build and publish shared self-hosted ZFS akmods image | `akmods-build-and-publish` | `ci_tools.akmods_build_and_publish` |

## Workflow Map

- [`build.yml`](../workflows/build.yml)
  - main candidate build, promotion, and signing
- [`build-branch.yml`](../workflows/build-branch.yml)
  - branch-tagged push using read-only shared-cache validation
- [`build-pr.yml`](../workflows/build-pr.yml)
  - no-push validation build

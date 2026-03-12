# Code Reading Guide

If a term is unfamiliar, check the shared glossary first:
[`docs/glossary.md`](./glossary.md)

## Read This Repo In This Order

### 1. Main workflow

- [`.github/workflows/build.yml`](../.github/workflows/build.yml)

### 2. Command map and command-line interface (CLI)

- [`.github/scripts/README.md`](../.github/scripts/README.md)
- [`ci/defaults.json`](../ci/defaults.json)
- [`ci_tools/cli.py`](../ci_tools/cli.py)

### 3. Input resolution and cache checks

1. [`ci_tools/resolve_build_inputs.py`](../ci_tools/resolve_build_inputs.py)
2. [`ci_tools/write_build_inputs_manifest.py`](../ci_tools/write_build_inputs_manifest.py)
3. [`ci_tools/check_akmods_cache.py`](../ci_tools/check_akmods_cache.py)
4. [`ci_tools/prepare_validation_build.py`](../ci_tools/prepare_validation_build.py)
   - read-only cache validation plus pinned-akmods-ref fetch validation
5. [`ci_tools/export_registry_context.py`](../ci_tools/export_registry_context.py)
   - normalizes registry paths and detects automation accounts

### 4. Akmods build control

1. [`ci_tools/akmods_clone_pinned.py`](../ci_tools/akmods_clone_pinned.py)
   - clones the pinned upstream fork and verifies the exact commit SHA
2. [`ci_tools/akmods_configure_zfs_target.py`](../ci_tools/akmods_configure_zfs_target.py)
3. [`ci_tools/akmods_build_and_publish.py`](../ci_tools/akmods_build_and_publish.py)

### 5. Native image composition

1. [`Containerfile`](../Containerfile)
2. [`build_files/build-image.sh`](../build_files/build-image.sh)
3. [`files/scripts/configure_signing_policy.py`](../files/scripts/configure_signing_policy.py)
4. [`containerfiles/zfs-akmods/install_zfs_from_akmods_cache.py`](../containerfiles/zfs-akmods/install_zfs_from_akmods_cache.py)
5. [`shared/oci_layout.py`](../shared/oci_layout.py)

### 6. Tagging and signing

1. [`ci_tools/compute_branch_metadata.py`](../ci_tools/compute_branch_metadata.py)
2. [`ci_tools/compose_branch_image_tag.py`](../ci_tools/compose_branch_image_tag.py)
3. [`ci_tools/compute_candidate_tag.py`](../ci_tools/compute_candidate_tag.py)
4. [`ci_tools/promote_stable.py`](../ci_tools/promote_stable.py)
5. [`ci_tools/sign_image.py`](../ci_tools/sign_image.py)
   - used by branch/main publish flows when the signing secret is present
6. [`.github/actions/`](../.github/actions)
   - local composite actions used to keep the workflow files readable

### 7. Tests

1. [`tests/test_resolve_build_inputs.py`](../tests/test_resolve_build_inputs.py)
2. [`tests/test_check_akmods_cache.py`](../tests/test_check_akmods_cache.py)
3. [`tests/test_compute_branch_metadata.py`](../tests/test_compute_branch_metadata.py)
4. [`tests/test_compute_candidate_tag.py`](../tests/test_compute_candidate_tag.py)
5. [`tests/test_compose_branch_image_tag.py`](../tests/test_compose_branch_image_tag.py)
6. [`tests/test_export_registry_context.py`](../tests/test_export_registry_context.py)
7. [`tests/test_configure_signing_policy.py`](../tests/test_configure_signing_policy.py)
8. [`tests/test_promote_stable.py`](../tests/test_promote_stable.py)
9. [`tests/test_sign_image.py`](../tests/test_sign_image.py)
10. [`tests/test_install_zfs_from_akmods_cache.py`](../tests/test_install_zfs_from_akmods_cache.py)

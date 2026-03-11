# Code Reading Guide

If a term is unfamiliar, check the shared glossary first:
[`docs/glossary.md`](./glossary.md)

## Read This Repo In This Order

### 1. Main workflow

- [`.github/workflows/build.yml`](../.github/workflows/build.yml)

### 2. Command map and CLI

- [`.github/scripts/README.md`](../.github/scripts/README.md)
- [`ci_tools/cli.py`](../ci_tools/cli.py)

### 3. Input resolution and cache checks

1. [`ci_tools/resolve_build_inputs.py`](../ci_tools/resolve_build_inputs.py)
2. [`ci_tools/write_build_inputs_manifest.py`](../ci_tools/write_build_inputs_manifest.py)
3. [`ci_tools/check_akmods_cache.py`](../ci_tools/check_akmods_cache.py)
4. [`ci_tools/prepare_validation_build.py`](../ci_tools/prepare_validation_build.py)

### 4. Akmods build control

1. [`ci_tools/akmods_clone_pinned.py`](../ci_tools/akmods_clone_pinned.py)
2. [`ci_tools/akmods_configure_zfs_target.py`](../ci_tools/akmods_configure_zfs_target.py)
3. [`ci_tools/akmods_build_and_publish.py`](../ci_tools/akmods_build_and_publish.py)

### 5. Native image composition

1. [`Containerfile`](../Containerfile)
2. [`build_files/build-image.sh`](../build_files/build-image.sh)
3. [`files/scripts/configure-signing-policy.sh`](../files/scripts/configure-signing-policy.sh)
4. [`containerfiles/zfs-akmods/install_zfs_from_akmods_cache.py`](../containerfiles/zfs-akmods/install_zfs_from_akmods_cache.py)

### 6. Tagging and signing

1. [`ci_tools/compute_branch_metadata.py`](../ci_tools/compute_branch_metadata.py)
2. [`ci_tools/promote_stable.py`](../ci_tools/promote_stable.py)
3. [`ci_tools/sign_image.py`](../ci_tools/sign_image.py)

### 7. Tests

1. [`tests/test_resolve_build_inputs.py`](../tests/test_resolve_build_inputs.py)
2. [`tests/test_check_akmods_cache.py`](../tests/test_check_akmods_cache.py)
3. [`tests/test_compute_branch_metadata.py`](../tests/test_compute_branch_metadata.py)
4. [`tests/test_promote_stable.py`](../tests/test_promote_stable.py)
5. [`tests/test_sign_image.py`](../tests/test_sign_image.py)
6. [`tests/test_install_zfs_from_akmods_cache.py`](../tests/test_install_zfs_from_akmods_cache.py)

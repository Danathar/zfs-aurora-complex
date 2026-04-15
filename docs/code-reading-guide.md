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
   - pull-request cache validation plus pinned-akmods-ref fetch validation
5. [`ci_tools/tagging_context.py`](../ci_tools/tagging_context.py)
   - normalizes registry paths and detects automation accounts

### 4. Akmods build control

1. [`ci_tools/akmods_clone_pinned.py`](../ci_tools/akmods_clone_pinned.py)
   - clones the resolved upstream akmods commit and verifies the exact SHA
2. [`ci_tools/akmods_configure_zfs_target.py`](../ci_tools/akmods_configure_zfs_target.py)
3. [`ci_tools/akmods_build_and_publish.py`](../ci_tools/akmods_build_and_publish.py)

### 5. Native image composition

1. [`Containerfile`](../Containerfile)
2. [`build_files/build-image.sh`](../build_files/build-image.sh)
3. [`files/scripts/configure_signing_policy.py`](../files/scripts/configure_signing_policy.py)
4. [`containerfiles/zfs-akmods/install_zfs_from_akmods_cache.py`](../containerfiles/zfs-akmods/install_zfs_from_akmods_cache.py)
5. [`shared/oci_layout.py`](../shared/oci_layout.py)

### 6. Tagging and signing

1. [`ci_tools/tagging_context.py`](../ci_tools/tagging_context.py)
   - builds candidate tags, branch tags, branch-safe prefixes, and registry context values
2. [`ci_tools/promote_stable.py`](../ci_tools/promote_stable.py)
3. [`ci_tools/sign_image.py`](../ci_tools/sign_image.py)
   - used by branch/main publish flows when the signing secret is present
4. [`.github/actions/`](../.github/actions)
   - local composite actions used to keep the workflow files readable

### 7. Failure triage

1. [`.github/workflows/akmods-failure-triage.yml`](../.github/workflows/akmods-failure-triage.yml)
   - opens or updates sticky GitHub issues for classified akmods build failures
2. [`ci_tools/classify_akmods_failure.py`](../ci_tools/classify_akmods_failure.py)
   - turns akmods build logs into a small issue payload for the triage workflow

### 8. Tests

1. [`tests/test_akmods_build_and_publish.py`](../tests/test_akmods_build_and_publish.py)
2. [`tests/test_akmods_clone_pinned.py`](../tests/test_akmods_clone_pinned.py)
3. [`tests/test_akmods_configure_zfs_target.py`](../tests/test_akmods_configure_zfs_target.py)
4. [`tests/test_check_akmods_cache.py`](../tests/test_check_akmods_cache.py)
5. [`tests/test_classify_akmods_failure.py`](../tests/test_classify_akmods_failure.py)
6. [`tests/test_cli.py`](../tests/test_cli.py)
7. [`tests/test_common.py`](../tests/test_common.py)
8. [`tests/test_configure_signing_policy.py`](../tests/test_configure_signing_policy.py)
9. [`tests/test_export_repo_defaults.py`](../tests/test_export_repo_defaults.py)
10. [`tests/test_install_zfs_from_akmods_cache.py`](../tests/test_install_zfs_from_akmods_cache.py)
11. [`tests/test_prepare_validation_build.py`](../tests/test_prepare_validation_build.py)
12. [`tests/test_promote_stable.py`](../tests/test_promote_stable.py)
13. [`tests/test_resolve_build_inputs.py`](../tests/test_resolve_build_inputs.py)
14. [`tests/test_sign_image.py`](../tests/test_sign_image.py)
15. [`tests/test_tagging_context.py`](../tests/test_tagging_context.py)
16. [`tests/test_write_build_inputs_manifest.py`](../tests/test_write_build_inputs_manifest.py)

#### Running Tests

```bash
pip install pytest
python3 -m pytest tests/ -v
```

Tests use `unittest.TestCase` with `unittest.mock.patch` and have no external
dependencies beyond `pytest` as the test runner. Every CI tool module in
`ci_tools/` has a corresponding `tests/test_<module_name>.py` file. All external
calls (subprocess, registry, filesystem) are mocked so tests run without network
access or container tooling.

Tests run automatically in CI via
[`.github/workflows/test.yml`](../.github/workflows/test.yml) on every pull
request and push to `main`.

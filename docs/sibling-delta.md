# Sibling delta

If a term is unfamiliar, check the shared glossary first:
[`docs/glossary.md`](./glossary.md)

This repository and
[`zfs-kinoite-complex`](https://github.com/Danathar/zfs-kinoite-complex), its
sister project, build the same ZFS image on two different desktops. They
started as copies, and most of `ci_tools/` is still the same code in both. A
fix made to a shared module in one repository does not reach the other on its
own, and that has already let safety fixes land in one and not the other.

This page is the record of where the two copies differ, and whether each
difference is meant. It has one row per `ci_tools/*.py` module in this
repository. `tests/test_sibling_delta_doc.py` fails when a module has no row,
when a row names a module that no longer exists, or when a status is not one
of the forms below.

**When to update it:** in the same pull request that changes a `ci_tools/`
module, and whenever you compare the two repositories. If the change applies
to the sister repository too, open a port issue there and mark the row
`port pending`. If it does not, say why in the row.

**Statuses:**

- `same` -- the file is byte-identical in both repositories.
- `differs on purpose: <reason>` -- the difference is deliberate, such as the
  repository's own name or an input only one of them builds with.
- `port pending: <issue>` -- one side is behind. The issue in this repository
  that tracks the port is named as `#N`; when the sister is the one behind,
  that issue links the sister's port issue.
- `only here: <reason>` -- the sister repository has no copy.

Last sister commit reviewed: `af6e15f00b267019259eb460448e932d7ec06e73`

## Modules

| Module | Status | Note |
|---|---|---|
| `__init__.py` | same | |
| `akmods_build_and_publish.py` | same | |
| `akmods_clone_pinned.py` | port pending: #209 | Behind here: no timeout on the akmods `git fetch` (sister 0a05e37). |
| `akmods_configure_zfs_target.py` | same | |
| `boot_test.py` | port pending: #209 | The `ZAC` names and the Aurora wording differ on purpose. Behind here: the image pull uses `podman pull --retry` and the job's time limit, because `run_cmd` has no timeout yet; the sister uses `run_cmd_with_retries` with a pull timeout (sister 0a05e37, cfdb192). |
| `check_akmods_cache.py` | port pending: #209 | Both copies now require the registry login (the sister since af6e15f). Behind here: the shared credential helper and the cache-tag helper from `pin_akmods_cache.py` (sister 3b5c1c7). The comment about who can republish the cache differs on purpose: this repository builds the cache only on `main`. |
| `check_stable_signal.py` | port pending: #209 | Label names and Aurora wording differ on purpose. Behind here: builds the registry login inline instead of with the shared credential helper (sister 3b5c1c7). |
| `classify_akmods_failure.py` | differs on purpose: the same transient-network fix landed separately in each repository; the wording of the summary and docstrings differs, and this repository matches the curl error case-insensitively | The kinds and their precedence are the same. |
| `cli.py` | differs on purpose: the sister has a `prune-registry` command this repository does not | |
| `common.py` | port pending: #209 | Behind here: no `timeout=` on `run_cmd`/`run_json_cmd`, no `*_TIMEOUT` ceilings and no `run_cmd_with_retries` (sister 0a05e37, 43c9350, cfdb192); no `registry_creds_from_env`, and `registry_auth_dir` does not refuse a login that names no registry (sister 3b5c1c7, 5ca41bd). |
| `export_repo_defaults.py` | same | |
| `pin_akmods_cache.py` | port pending: #209 | Behind here: no timeout on the registry read (sister 0a05e37). |
| `prepare_validation_build.py` | differs on purpose: the same private-package hint for a registry permission error landed separately in each repository (the sister's in af6e15f); the comment wording differs, and only this copy's hint adds that the image build also pulls the package anonymously | |
| `promote_stable.py` | port pending: #209 | Behind here: no refusal to move `latest` back to an older run (sister 8a9a09d); no timeout on `cosign verify` (sister 43c9350); no shared credential helper (sister 3b5c1c7). The sister imports `STABLE_RE` from `prune_registry.py`, which this repository does not have. |
| `resolve_build_inputs.py` | port pending: #209 | The sister's brew image inputs differ on purpose. Behind here: no explicit, retried `podman pull` before the kernel listing (sister cfdb192). |
| `sign_image.py` | port pending: #209 | Behind here: no timeout on `cosign sign`/`cosign verify` (sister 0a05e37, 43c9350). |
| `tagging_context.py` | same | |
| `write_akmods_badge.py` | same | |
| `write_build_inputs_manifest.py` | differs on purpose: the sister records its brew image inputs, which this repository does not build with | |
| `write_last_good_build_badge.py` | port pending: #209 | Behind here: builds the optional registry login inline instead of with the shared credential helper (sister 3b5c1c7). |
| `zfs_release.py` | differs on purpose: the GitHub API User-Agent names each repository | |

The sister repository also has `prune_registry.py`, which removes old image
versions from the registry. This repository has no copy. The table lists only
the modules this repository has, so whether to adopt it is a separate
decision, not a row.

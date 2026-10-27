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
repository, and one row per shared build step (`.github/actions/<name>/action.yml`).
`tests/test_sibling_delta_doc.py` fails when a module or build step has no
row, when a row names one that no longer exists, or when a status is not one
of the forms below.

**When to update it:** in the same pull request that changes a `ci_tools/`
module or a `.github/actions/` build step, and whenever you compare the two
repositories. If the change applies
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

Last sister commit reviewed: `7b8ad5003c5bc8ffe053a81b5b8c86747e186fc4`

## Modules

| Module | Status | Note |
|---|---|---|
| `__init__.py` | same | |
| `akmods_build_and_publish.py` | same | |
| `akmods_clone_pinned.py` | differs on purpose: the comment on the fetch timeout says the job's own timeout is what else bounds it (the jobs that run it here set `timeout-minutes`), where the sister's comment names GitHub's 360-minute default | The `git fetch` timeout itself (sister 0a05e37) is the same. |
| `akmods_configure_zfs_target.py` | same | |
| `boot_test.py` | differs on purpose: the `ZAC` names and the Aurora wording; this copy also records that stock aurora-dx passes the same checks, and says sshd is started whether or not the image enables it | The image pull is the sister's `run_cmd_with_retries` with a pull timeout (sister 0a05e37, cfdb192). |
| `check_akmods_cache.py` | differs on purpose: `main()` reads the required login once and passes it to `inspect_akmods_cache` as `creds`, where the sister's `inspect_akmods_cache` reads it from the environment itself; the docstring and comments explaining that differ to match, the comment about who can republish the cache says this repository builds the cache only on `main`, and the refusal names this repository's ZFS version label | Both copies require the login through the shared credential helper and build the cache tag with `akmods_cache_image_tag` (sister 3b5c1c7, 0059e42, af6e15f). |
| `check_stable_signal.py` | differs on purpose: label names and Aurora wording | The shared credential helper (sister 3b5c1c7) is ported. |
| `classify_akmods_failure.py` | differs on purpose: the same transient-network fix landed separately in each repository; the wording of the summary and docstrings differs, and this repository matches the curl error case-insensitively | The kinds and their precedence are the same. |
| `cli.py` | differs on purpose: the sister has `prune-registry` and `check-ruleset-drift` commands this repository does not, and passes the options written after a command name to `check-ruleset-drift` | The merge-rule drift check (sister b6220f7, 3afefd3) runs only in the sister, which checks this repository's rules too. |
| `common.py` | differs on purpose: comments and docstrings only; this copy's timeout comments say the job's own `timeout-minutes` is the other bound and name no sister run, and it leaves out the sister's record of the skopeo and cosign versions it checked the auth file against, which was made against the sister's own image | The timeouts and `run_cmd_with_retries` (sister 0a05e37, 43c9350, cfdb192), `registry_creds_from_env` and the refusal of a login that names no registry (sister 3b5c1c7, 5ca41bd), the `registry_auth_dir` docstring (sister fd7d7a4), and `skopeo_inspect_json` skipping the tag list (sister 27fff1c) are ported. |
| `export_repo_defaults.py` | same | |
| `pin_akmods_cache.py` | same | |
| `prepare_validation_build.py` | differs on purpose: the same private-package hint for a registry permission error landed separately in each repository (the sister's in af6e15f); the comment wording differs, and only this copy's hint adds that the image build also pulls the package anonymously | |
| `promote_stable.py` | differs on purpose: this copy defines `STABLE_RE` itself, because the sister imports it from `prune_registry.py`, which this repository does not have | The refusal to move `latest` back to an older run (sister 8a9a09d), the `cosign verify` timeout (sister 43c9350) and the shared credential helper (sister 3b5c1c7) are ported. |
| `resolve_build_inputs.py` | differs on purpose: the sister's brew image inputs; the docstring of the retried base-image pull names no sister run or job length | The explicit, retried `podman pull` before the kernel listing is ported, with its per-attempt share of the transfer timeout (sister cfdb192, 05b2fd9). |
| `sign_image.py` | same | |
| `tagging_context.py` | same | |
| `write_akmods_badge.py` | same | |
| `write_build_inputs_manifest.py` | differs on purpose: the sister records its brew image inputs, which this repository does not build with | |
| `write_last_good_build_badge.py` | same | |
| `zfs_release.py` | differs on purpose: the GitHub API User-Agent names each repository | |

The sister repository also has `prune_registry.py`, which removes old image
versions from the registry. This repository has no copy. The table lists only
the modules this repository has, so whether to adopt it is a separate
decision, not a row.

`tests/run_tests.py` is not a `ci_tools/` module, so it has no row, but it is
shared with the sister too and kept in step by hand. Both copies refuse the
same pytest options and list every other option they forward in `FORWARDED`,
which `tests/test_run_tests.py` holds against the installed pytest. This copy
forwards the four options typeguard 4.4.4, the version on the CI runner,
offers, and no other typeguard option; compare the two sets when either side
changes one.

## Shared build steps

The composite actions the workflows run. Both repositories have all eight,
under the same names.

| Build step | Status | Note |
|---|---|---|
| `build-native-image` | differs on purpose: the sister builds its Homebrew payload in (a `brew_image` input, the `BREW_IMAGE` build argument and its label), and the image labels and description name each repository | |
| `install-signing-tools` | same | Both repositories' `renovate.json` track the `cosign-release` pin with a `sigstore/cosign` manager and never automerge it (sister a32987f). |
| `load-ci-defaults` | same | |
| `prepare-main-akmods` | differs on purpose: the sister has Homebrew payload outputs (`brew_image_ref`, `brew_image_pinned`, `brew_image_digest`) this repository does not build with; the comments on the `allow_rebuild` input, the credential passing, the failure kinds and the ZFS version label differ in wording or repository name only | Both install the pinned `just` (#151; sister 2f04bdc, its #513). |
| `prepare-rechunk-host` | same | |
| `prepare-registry-context` | same | |
| `publish-native-image` | differs on purpose: comments only (each names its own issue for the signing split), and the push step lists `id` before `name` here | |
| `rechunk-native-image` | differs on purpose: comments only; they name which project's base hit E2BIG and which project's nightly the Chunkah pull failed in | Both pass only `.Config` to Chunkah (#72; sister 2f04bdc, its #513) and pull Chunkah explicitly with `--retry 5 --retry-delay 30s` before `podman run` (sister 69caea7, its #487). |

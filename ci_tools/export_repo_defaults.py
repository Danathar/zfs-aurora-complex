"""
Script: ci_tools/export_repo_defaults.py
What: Loads the checked-in repository defaults and exports them for GitHub Actions.
Doing: Reads `ci/defaults.json`, writes lowercase step outputs, and writes the original env-style names to `GITHUB_ENV`.
Why: Keeps the workflow files short while preserving one version-controlled source of truth for defaults.
Goal: Let workflows and helper scripts agree on the same default refs and image names.
"""

from __future__ import annotations

from ci_tools.common import load_repo_defaults, write_github_env, write_github_outputs


OUTPUT_NAME_MAP = {
    "AKMODS_UPSTREAM_REPO": "akmods_upstream_repo",
    "AKMODS_UPSTREAM_REF": "akmods_upstream_ref",
    "IMAGE_NAME": "image_name",
    "AKMODS_REPO": "akmods_repo",
    "DEFAULT_BASE_IMAGE": "default_base_image",
    "DEFAULT_BUILD_CONTAINER_IMAGE": "default_build_container_image",
    "DEFAULT_BREW_IMAGE": "default_brew_image",
    "DEFAULT_ZFS_MINOR_VERSION": "default_zfs_minor_version",
}


def main() -> None:
    defaults = load_repo_defaults()

    # Export the original env-style names so later shell steps can read them
    # directly without each workflow having to restate the same constants.
    write_github_env({key: defaults[key] for key in OUTPUT_NAME_MAP})

    # GitHub step outputs are easier to reference from `with:` and `if:` blocks
    # than runtime environment variables, so we expose a lowercase map as well.
    write_github_outputs({output_name: defaults[key] for key, output_name in OUTPUT_NAME_MAP.items()})

    print(f"Loaded repository defaults from ci/defaults.json ({len(OUTPUT_NAME_MAP)} values).")


if __name__ == "__main__":
    main()

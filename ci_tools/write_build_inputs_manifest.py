"""
Script: ci_tools/write_build_inputs_manifest.py
What: Writes a JSON record of the exact inputs used for this run.
Doing: Collects workflow/run metadata and resolved image refs, then writes `artifacts/build-inputs.json`.
Why: Makes failed runs easier to investigate and rerun with the same inputs.
Goal: Save an exact input record for each run.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ci_tools.common import require_env


ARTIFACT_DIR = Path("artifacts")
ARTIFACT_PATH = ARTIFACT_DIR / "build-inputs.json"


def main() -> None:
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    document = {
        "schema_version": 1,
        "generated_at": generated_at,
        "repository": require_env("GITHUB_REPOSITORY"),
        "workflow": require_env("GITHUB_WORKFLOW"),
        "run": {
            "id": int(require_env("GITHUB_RUN_ID")),
            "attempt": int(require_env("GITHUB_RUN_ATTEMPT")),
            "number": int(require_env("GITHUB_RUN_NUMBER")),
            "ref": require_env("GITHUB_REF"),
            "sha": require_env("GITHUB_SHA"),
            "actor": require_env("GITHUB_ACTOR"),
        },
        "inputs": {
            "use_input_lock": require_env("USE_INPUT_LOCK").lower() == "true",
            "lock_file_path": require_env("LOCK_FILE_PATH"),
            "fedora_version": require_env("FEDORA_VERSION"),
            "kernel_release": require_env("KERNEL_RELEASE"),
            "kernel_releases": require_env("KERNEL_RELEASES").split(),
            "base_image_ref": require_env("BASE_IMAGE_REF"),
            "base_image_name": require_env("BASE_IMAGE_NAME"),
            "base_image_tag": require_env("BASE_IMAGE_TAG"),
            "base_image_pinned": require_env("BASE_IMAGE_PINNED"),
            "base_image_digest": require_env("BASE_IMAGE_DIGEST"),
            "build_container_ref": require_env("BUILD_CONTAINER_REF"),
            "build_container_pinned": require_env("BUILD_CONTAINER_PINNED"),
            "build_container_digest": require_env("BUILD_CONTAINER_DIGEST"),
            "zfs_minor_version": require_env("ZFS_MINOR_VERSION"),
            "akmods_upstream_ref": require_env("AKMODS_UPSTREAM_REF"),
        },
    }

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    ARTIFACT_PATH.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    print(ARTIFACT_PATH.read_text(encoding="utf-8"), end="")


if __name__ == "__main__":
    main()

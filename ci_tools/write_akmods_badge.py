"""
Script: ci_tools/write_akmods_badge.py
What: Builds a shields.io endpoint-badge JSON payload reflecting OpenZFS/kernel compat state.
Doing: Reads the build workflow's conclusion and (when present) the akmods failure payload,
    then writes a small JSON file in shields.io's dynamic-badge schema.
Why: The README badge should show the specific reason a build is blocked (not just red/green),
    without duplicating the parsing already done in classify_akmods_failure.py.
Goal: Give a reader glancing at the README the same answer the sticky issue already has.
"""

from __future__ import annotations

import json
from pathlib import Path

from ci_tools.classify_akmods_failure import FAILURE_KIND_UPSTREAM_COMPAT
from ci_tools.common import optional_env, write_github_outputs

BADGE_LABEL = "openzfs/kernel"


def build_badge_payload(*, conclusion: str, failure_payload: dict | None) -> dict | None:
    """
    Return a shields.io endpoint-badge payload, or None when the badge should be left alone.

    Only two states are ever rendered: a classified upstream-compat failure (red, with the
    specific versions involved), and a successful build (green). Any other conclusion
    (unclassified failure, cancelled run, etc.) is not this badge's concern, since it does not
    tell us anything new about OpenZFS/kernel compatibility.
    """
    if conclusion == "success":
        return {
            "schemaVersion": 1,
            "label": BADGE_LABEL,
            "message": "in sync",
            "color": "brightgreen",
        }

    is_upstream_compat_failure = (
        conclusion == "failure"
        and failure_payload
        and failure_payload.get("failure_kind") == FAILURE_KIND_UPSTREAM_COMPAT
    )
    if is_upstream_compat_failure:
        zfs_version = failure_payload.get("zfs_version") or ""
        max_kernel = failure_payload.get("max_kernel") or ""
        kernel_release = failure_payload.get("kernel_release") or "current kernel"
        if zfs_version and max_kernel:
            message = (
                f"waiting: OpenZFS {zfs_version} caps at {max_kernel}, image is {kernel_release}"
            )
        else:
            message = "waiting: known upstream ZFS/kernel incompatibility"
        return {
            "schemaVersion": 1,
            "label": BADGE_LABEL,
            "message": message,
            "color": "red",
        }

    return None


def main() -> None:
    conclusion = optional_env("WORKFLOW_CONCLUSION")
    failure_payload_path = optional_env("FAILURE_PAYLOAD_PATH")
    badge_output_path = optional_env("BADGE_OUTPUT_PATH") or "artifacts/akmods-badge.json"

    failure_payload: dict | None = None
    if failure_payload_path and Path(failure_payload_path).exists():
        failure_payload = json.loads(Path(failure_payload_path).read_text(encoding="utf-8"))

    badge = build_badge_payload(conclusion=conclusion, failure_payload=failure_payload)

    if badge is None:
        write_github_outputs({"updated": "false"})
        print(
            f"Conclusion {conclusion!r} does not change the OpenZFS/kernel badge; leaving it as-is."
        )
        return

    out_path = Path(badge_output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(badge, indent=2) + "\n", encoding="utf-8")

    write_github_outputs({"updated": "true", "badge_path": str(out_path)})
    print(f"Wrote badge payload to {out_path}: {badge['message']!r}")


if __name__ == "__main__":
    main()

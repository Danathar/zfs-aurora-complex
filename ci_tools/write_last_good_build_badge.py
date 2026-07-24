"""
Script: ci_tools/write_last_good_build_badge.py
What: Builds a shields.io endpoint-badge JSON payload showing the age of the live `:latest` image.
Doing: Inspects the published `:latest` image's `Created` timestamp and computes days since then.
Why: `latest` only moves on a real promotion (see docs/upstream-change-response.md), so during an
    outage it can sit unchanged for a while. A reader should be able to see, from the README alone,
    that a working image is still available and exactly how stale it is.
Goal: Give an always-current answer without tracking any state of our own — the registry is the
    single source of truth for when the current `latest` was actually built.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ci_tools.common import (
    normalize_owner,
    optional_env,
    require_env,
    require_env_or_default,
    skopeo_inspect_json_optional,
    write_github_outputs,
)

BADGE_LABEL = "last good build"


def build_last_good_build_badge(*, created_iso: str, now: datetime) -> dict | None:
    """Return a shields.io endpoint-badge payload for the `:latest` image's age, or None if unknown."""
    if not created_iso:
        return None

    created = datetime.fromisoformat(created_iso.replace("Z", "+00:00"))
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    days = (now.date() - created.date()).days
    if days <= 0:
        age = "today"
    elif days == 1:
        age = "1 day ago"
    else:
        age = f"{days} days ago"

    return {
        "schemaVersion": 1,
        "label": BADGE_LABEL,
        "message": f"{created.date().isoformat()} ({age})",
        "color": "brightgreen",
    }


def main() -> None:
    image_name = require_env_or_default("IMAGE_NAME")
    image_org = normalize_owner(require_env("GITHUB_REPOSITORY_OWNER"))
    registry_actor = optional_env("REGISTRY_ACTOR")
    registry_token = optional_env("REGISTRY_TOKEN")
    creds = f"{registry_actor}:{registry_token}" if registry_actor and registry_token else None
    badge_output_path = optional_env("BADGE_OUTPUT_PATH") or "artifacts/last-good-build-badge.json"

    image_ref = f"docker://ghcr.io/{image_org}/{image_name}:latest"
    inspect_json = skopeo_inspect_json_optional(image_ref, creds=creds)
    created_iso = str((inspect_json or {}).get("Created") or "")

    badge = build_last_good_build_badge(created_iso=created_iso, now=datetime.now(timezone.utc))

    if badge is None:
        write_github_outputs({"updated": "false"})
        print(f"Could not read a Created timestamp from {image_ref}; leaving badge as-is.")
        return

    out_path = Path(badge_output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(badge, indent=2) + "\n", encoding="utf-8")

    write_github_outputs({"updated": "true", "badge_path": str(out_path)})
    print(f"Wrote last-good-build badge to {out_path}: {badge['message']!r}")


if __name__ == "__main__":
    main()

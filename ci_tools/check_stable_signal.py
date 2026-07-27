"""
Script: ci_tools/check_stable_signal.py
What: Decides whether the scheduled production workflow should build.
Doing: Compares the current upstream stable-signal digest, and the current
resolved ZFS patch version, against what is recorded on this repo's latest
promoted image.
Why: Scheduled runs should only spend build time when Aurora stable has
advanced OR a new OpenZFS patch is available on the configured minor line --
otherwise a patch release (including a security fix) could sit unbuilt
indefinitely while the base image itself stayed still.
Goal: Fail closed on unknown upstream state and skip only truly unchanged
scheduled runs.
"""

from __future__ import annotations

from dataclasses import dataclass

from ci_tools.common import (
    CiToolError,
    normalize_owner,
    optional_env,
    require_env,
    require_env_or_default,
    skopeo_inspect_json,
    skopeo_inspect_json_optional,
    write_github_outputs,
)
from ci_tools.zfs_release import resolve_latest_zfs_version

STABLE_SIGNAL_IMAGE_LABEL = "org.zfs-aurora-complex.stable-signal-image"
STABLE_SIGNAL_DIGEST_LABEL = "org.zfs-aurora-complex.stable-signal-digest"
ZFS_VERSION_LABEL = "org.zfs-aurora-complex.zfs-version"


@dataclass(frozen=True)
class StableSignalDecision:
    """
    Resolved decision and metadata for the scheduled-build gate.

    `zfs_version` is the current newest release the gate resolved on the
    configured minor line. It is only meaningful for the schedule-event path:
    non-schedule events leave it "" because the actual `zfs-version` image
    label is sourced from the real build's own resolution (see
    `build-zfs-akmods` in `build.yml`), not from this gate, so resolving it
    here a second time would just be a wasted network call.
    """

    should_build: bool
    reason: str
    stable_signal_ref: str
    stable_signal_digest: str
    zfs_version: str


def _bypass_decision(stable_signal_image: str) -> StableSignalDecision:
    """
    Always build on push/manual events, but still record stable-signal provenance.

    Without a digest here, the candidate's stable-signal-digest label (and the
    label promoted onto `:latest`) would be empty, so the next scheduled run
    would always see "current-latest-missing-stable-signal-labels" and do a
    full rebuild even when Aurora stable never moved. The lookup is
    best-effort: a registry hiccup must not fail a push/manual build, so any
    failure here just falls back to the previous empty-digest behavior.
    """
    stable_signal_digest = ""
    try:
        inspect_json = skopeo_inspect_json_optional(_docker_ref(stable_signal_image))
        if inspect_json is not None:
            stable_signal_digest = str(inspect_json.get("Digest") or "")
    except CiToolError:
        stable_signal_digest = ""

    return StableSignalDecision(
        should_build=True,
        reason="not-schedule-event",
        stable_signal_ref=stable_signal_image,
        stable_signal_digest=stable_signal_digest,
        zfs_version="",
    )


def _docker_ref(image_ref: str) -> str:
    """Return a `docker://` registry ref for `skopeo` commands."""
    if image_ref.startswith("docker://"):
        return image_ref
    return f"docker://{image_ref}"


def evaluate_stable_signal_gate(
    *,
    image_org: str,
    image_name: str,
    stable_signal_image: str,
    zfs_minor_version: str,
    creds: str,
) -> StableSignalDecision:
    """
    Compare upstream stable-signal state, AND the current resolved ZFS patch
    version, against the last promoted image's labels.

    The upstream stable-signal image is authoritative for base-image cadence.
    Independently, the newest release on the configured ZFS minor line is
    authoritative for ZFS cadence: a scheduled run must build when either one
    has moved, not just when the base image has. Without the ZFS check, a new
    OpenZFS patch (including a security fix) could sit unbuilt indefinitely as
    long as Aurora stable itself did not move.

    The repo's own `:latest` image is only a provenance source for the last
    promoted values of both signals.
    """
    stable_signal_inspect = skopeo_inspect_json(_docker_ref(stable_signal_image))
    stable_signal_digest = str(stable_signal_inspect.get("Digest") or "")
    if not stable_signal_digest:
        raise CiToolError(
            f"Missing digest in skopeo inspect output for {_docker_ref(stable_signal_image)}"
        )

    current_zfs_version = resolve_latest_zfs_version(zfs_minor_version)

    current_latest = f"ghcr.io/{image_org}/{image_name}:latest"
    current_latest_inspect = skopeo_inspect_json_optional(_docker_ref(current_latest), creds=creds)
    if current_latest_inspect is None:
        return StableSignalDecision(
            should_build=True,
            reason="current-latest-missing",
            stable_signal_ref=stable_signal_image,
            stable_signal_digest=stable_signal_digest,
            zfs_version=current_zfs_version,
        )

    labels = current_latest_inspect.get("Labels") or {}
    previous_signal_image = str(labels.get(STABLE_SIGNAL_IMAGE_LABEL) or "")
    previous_signal_digest = str(labels.get(STABLE_SIGNAL_DIGEST_LABEL) or "")
    previous_zfs_version = str(labels.get(ZFS_VERSION_LABEL) or "")
    if not previous_signal_image or not previous_signal_digest:
        return StableSignalDecision(
            should_build=True,
            reason="current-latest-missing-stable-signal-labels",
            stable_signal_ref=stable_signal_image,
            stable_signal_digest=stable_signal_digest,
            zfs_version=current_zfs_version,
        )

    if not previous_zfs_version:
        return StableSignalDecision(
            should_build=True,
            reason="current-latest-missing-zfs-version-label",
            stable_signal_ref=stable_signal_image,
            stable_signal_digest=stable_signal_digest,
            zfs_version=current_zfs_version,
        )

    if previous_signal_image != stable_signal_image:
        return StableSignalDecision(
            should_build=True,
            reason="stable-signal-image-changed",
            stable_signal_ref=stable_signal_image,
            stable_signal_digest=stable_signal_digest,
            zfs_version=current_zfs_version,
        )

    if previous_zfs_version != current_zfs_version:
        return StableSignalDecision(
            should_build=True,
            reason="zfs-version-advanced",
            stable_signal_ref=stable_signal_image,
            stable_signal_digest=stable_signal_digest,
            zfs_version=current_zfs_version,
        )

    if previous_signal_digest == stable_signal_digest:
        return StableSignalDecision(
            should_build=False,
            reason="stable-signal-unchanged",
            stable_signal_ref=stable_signal_image,
            stable_signal_digest=stable_signal_digest,
            zfs_version=current_zfs_version,
        )

    return StableSignalDecision(
        should_build=True,
        reason="stable-signal-advanced",
        stable_signal_ref=stable_signal_image,
        stable_signal_digest=stable_signal_digest,
        zfs_version=current_zfs_version,
    )


def main() -> None:
    image_name = require_env_or_default("IMAGE_NAME")
    stable_signal_image = require_env_or_default("STABLE_SIGNAL_IMAGE")
    event_name = optional_env("GITHUB_EVENT_NAME")

    if event_name and event_name != "schedule":
        decision = _bypass_decision(stable_signal_image)
    else:
        image_org = normalize_owner(require_env("GITHUB_REPOSITORY_OWNER"))
        registry_actor = require_env("REGISTRY_ACTOR")
        registry_token = require_env("REGISTRY_TOKEN")
        creds = f"{registry_actor}:{registry_token}"
        zfs_minor_version = require_env_or_default("DEFAULT_ZFS_MINOR_VERSION")

        decision = evaluate_stable_signal_gate(
            image_org=image_org,
            image_name=image_name,
            stable_signal_image=stable_signal_image,
            zfs_minor_version=zfs_minor_version,
            creds=creds,
        )

    write_github_outputs(
        {
            "should_build": "true" if decision.should_build else "false",
            "reason": decision.reason,
            "stable_signal_ref": decision.stable_signal_ref,
            "stable_signal_digest": decision.stable_signal_digest,
            "zfs_version": decision.zfs_version,
        }
    )

    print(
        "Stable-signal decision: "
        f"should_build={decision.should_build} "
        f"reason={decision.reason} "
        f"stable_signal_ref={decision.stable_signal_ref} "
        f"stable_signal_digest={decision.stable_signal_digest} "
        f"zfs_version={decision.zfs_version}"
    )


if __name__ == "__main__":
    main()

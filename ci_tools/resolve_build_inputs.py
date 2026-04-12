"""
Script: ci_tools/resolve_build_inputs.py
What: Resolves and validates input references for one `main` workflow run.
Doing: Uses lock file or defaults, resolves digests/tags, and writes outputs.
Why: Keeps all jobs in the run on the same inputs.
Goal: Provide trusted base/build/kernel values for downstream steps.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from pathlib import Path
from typing import Callable

from ci_tools.common import (
    CiToolError,
    extract_fedora_version,
    git_ls_remote_resolve,
    load_repo_defaults,
    optional_env,
    require_env,
    require_env_or_default,
    run_cmd,
    skopeo_inspect_digest,
    skopeo_inspect_json,
    sort_kernel_releases,
    write_github_outputs,
)

TAG_FROM_REF_RE = re.compile(r"^[^@]+:([^/@]+)$")
DATE_STAMPED_TAG_RE = re.compile(r"-[0-9]{8}(\.[0-9]+)?$")
PLAIN_VERSION_LABEL_RE = re.compile(r"^(?P<fedora>[0-9]+)\.(?P<stamp>[0-9]{8}(?:\.[0-9]+)?)$")
PREFIXED_VERSION_LABEL_RE = re.compile(
    r"^(?P<prefix>[^-]+)-(?P<fedora>[0-9]+)\.(?P<stamp>[0-9]{8}(?:\.[0-9]+)?)$"
)


@dataclass(frozen=True)
class ResolvedBuildInputs:
    """
    Resolved values that downstream workflow jobs need for one build run.

    We keep this as a dataclass so multiple commands can share the same resolved
    values without each command re-implementing the environment and registry
    lookup flow.

    Important policy note:
    - `detected_kernel_releases` records every detected kernel directory in the base image
    - `kernel_release` is the supported primary kernel this repo builds and validates against
    """

    version: str
    kernel_release: str
    detected_kernel_releases: tuple[str, ...]
    base_image_ref: str
    base_image_name: str
    base_image_tag: str
    base_image_pinned: str
    base_image_digest: str
    build_container_ref: str
    build_container_pinned: str
    build_container_digest: str
    zfs_minor_version: str
    akmods_upstream_ref: str
    use_input_lock: bool
    lock_file_path: str


@dataclass(frozen=True)
class BuildInputResolution:
    """
    Full resolution result, including debug-only metadata used in logs.

    `label_kernel_release` and `candidate_tags` help explain why a particular
    immutable base tag was selected, but only `inputs` needs to travel to later
    workflow steps.
    """

    inputs: ResolvedBuildInputs
    label_kernel_release: str
    candidate_tags: tuple[str, ...]


def extract_source_tag(image_ref: str) -> str:
    """Return the tag from an image ref like `name:tag`, or empty string."""
    match = TAG_FROM_REF_RE.match(image_ref)
    return match.group(1) if match else ""


def choose_base_image_tag(
    *,
    source_tag: str,
    version_label: str,
    fedora_version: str,
    expected_digest: str,
    digest_lookup: Callable[[str], str],
) -> tuple[str, list[str]]:
    """
    Pick a stable base tag for this run.

    Rules:
    - If the source tag is already date-stamped, keep it.
    - Otherwise derive candidate tags from version label and choose the one
      that resolves to the expected digest.
    """
    # If we already got a date-stamped tag, treat it as stable for this run.
    if source_tag and DATE_STAMPED_TAG_RE.search(source_tag):
        return source_tag, [source_tag]

    candidate_tags: list[str] = []

    def append_candidate(tag: str) -> None:
        if tag and tag not in candidate_tags:
            candidate_tags.append(tag)

    prefixed_match = PREFIXED_VERSION_LABEL_RE.match(version_label)
    plain_match = PLAIN_VERSION_LABEL_RE.match(version_label)

    if prefixed_match:
        version_suffix = prefixed_match.group("stamp")
        label_prefix = prefixed_match.group("prefix")
        label_fedora_version = prefixed_match.group("fedora")
        normalized_suffix = version_suffix.split(".", 1)[0]
        append_candidate(version_label)
        append_candidate(f"{source_tag or label_prefix}-{version_suffix}")
        append_candidate(f"{fedora_version}-{version_suffix}")
        append_candidate(f"{fedora_version}-{label_fedora_version}.{version_suffix}")
        if normalized_suffix != version_suffix:
            append_candidate(f"{source_tag or label_prefix}-{label_fedora_version}.{normalized_suffix}")
            append_candidate(f"{source_tag or label_prefix}-{normalized_suffix}")
            append_candidate(f"{fedora_version}-{label_fedora_version}.{normalized_suffix}")
            append_candidate(f"{fedora_version}-{normalized_suffix}")
            append_candidate(f"{label_fedora_version}.{normalized_suffix}")
    elif plain_match:
        # Example label: 43.20260227.1
        # We only need the suffix part (20260227.1) to build candidate tags.
        version_suffix = plain_match.group("stamp")
        append_candidate(f"{source_tag}-{version_suffix}" if source_tag else "")
        append_candidate(f"latest-{version_suffix}")
        append_candidate(f"{fedora_version}-{version_suffix}")
    else:
        raise CiToolError(
            "Failed to derive immutable base tag from "
            f"org.opencontainers.image.version={version_label}"
        )

    # Try each candidate tag and keep the first one that resolves to the same digest.
    # Digest match is the key safety check: tag text can move, digest does not.
    for candidate_tag in candidate_tags:
        candidate_digest = digest_lookup(candidate_tag)
        if candidate_digest == expected_digest:
            return candidate_tag, candidate_tags

    raise CiToolError(
        f"Failed to map digest {expected_digest} to an immutable tag. "
        f"Tried candidate tags: {' '.join(candidate_tags)}"
    )


def _load_lock_file(lock_file_path: str) -> dict:
    # Lock file is a plain JSON object saved from a previous run.
    lock_path = Path(lock_file_path)
    if not lock_path.exists():
        raise CiToolError(f"Replay lock file not found: {lock_file_path}")
    with lock_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def detect_base_image_kernel_releases(image_ref: str) -> list[str]:
    """
    Inspect the base image filesystem and return every installed kernel release.

    We intentionally inspect `/lib/modules` from a real container view instead
    of trusting a single metadata label, because installonly kernel packages
    can leave more than one kernel in the final merged root filesystem.
    """
    output = run_cmd(
        [
            "podman",
            "run",
            "--rm",
            "--entrypoint",
            "/bin/sh",
            image_ref,
            "-lc",
            "find /lib/modules -mindepth 1 -maxdepth 1 -type d -printf '%f\\n'",
        ]
    )
    detected_kernel_releases = sort_kernel_releases(output.splitlines())
    if not detected_kernel_releases:
        raise CiToolError(f"No installed kernel directories found in {image_ref}")
    return detected_kernel_releases


def write_resolved_build_outputs(inputs: ResolvedBuildInputs) -> None:
    """
    Export resolved build values to GitHub step outputs.

    Keeping this in one helper ensures every workflow path writes the same
    output names, which keeps downstream jobs honest across main, branch, and
    PR validation.
    """

    write_github_outputs(
        {
            "version": inputs.version,
            "kernel_release": inputs.kernel_release,
            "detected_kernel_releases": " ".join(inputs.detected_kernel_releases),
            "base_image_ref": inputs.base_image_ref,
            "base_image_name": inputs.base_image_name,
            "base_image_tag": inputs.base_image_tag,
            "base_image_pinned": inputs.base_image_pinned,
            "base_image_digest": inputs.base_image_digest,
            "build_container_ref": inputs.build_container_ref,
            "build_container_pinned": inputs.build_container_pinned,
            "build_container_digest": inputs.build_container_digest,
            "zfs_minor_version": inputs.zfs_minor_version,
            "akmods_upstream_ref": inputs.akmods_upstream_ref,
            "use_input_lock": "true" if inputs.use_input_lock else "false",
            "lock_file_path": inputs.lock_file_path,
        }
    )


def _resolve_default_akmods_ref() -> str:
    """
    Pick the akmods source commit using a cascade that prefers explicit overrides.

    Order:
    1. `DEFAULT_AKMODS_REF` env (process-level override)
    2. `AKMODS_UPSTREAM_REF` env (explicit pin from caller)
    3. `AKMODS_UPSTREAM_REF` in `ci/defaults.json` (non-empty → explicit pin)
    4. `AKMODS_UPSTREAM_TRACK` (env or defaults) resolved via `git ls-remote` against `AKMODS_UPSTREAM_REPO`

    The point of (4) is self-healing: when no explicit pin is set, the build floats
    to whatever commit the tracking ref currently points at, so a red build caused
    by a transient upstream mismatch clears itself once upstream catches up.
    """
    explicit = optional_env("DEFAULT_AKMODS_REF") or optional_env("AKMODS_UPSTREAM_REF")
    if explicit:
        return explicit

    defaults = load_repo_defaults()
    pinned = defaults.get("AKMODS_UPSTREAM_REF", "").strip()
    if pinned:
        return pinned

    track = optional_env("AKMODS_UPSTREAM_TRACK") or defaults.get("AKMODS_UPSTREAM_TRACK", "").strip()
    if not track:
        raise CiToolError(
            "No akmods source commit is configured. Set AKMODS_UPSTREAM_REF (env or "
            "ci/defaults.json) or AKMODS_UPSTREAM_TRACK for floating resolution."
        )

    repo_url = optional_env("AKMODS_UPSTREAM_REPO") or defaults.get("AKMODS_UPSTREAM_REPO", "").strip()
    if not repo_url:
        raise CiToolError("AKMODS_UPSTREAM_REPO is required to resolve AKMODS_UPSTREAM_TRACK")

    return git_ls_remote_resolve(repo_url, track)


def resolve_configured_inputs() -> tuple[bool, str, str, str, str, str]:
    """
    Resolve top-level workflow inputs before any registry lookups happen.

    Return values are:
    1. lock replay mode flag
    2. lock file path
    3. build container ref
    4. base image ref
    5. ZFS minor version
    6. pinned akmods source ref
    """

    use_input_lock = optional_env("USE_INPUT_LOCK", "false").lower() == "true"
    lock_file_path = require_env("LOCK_FILE")
    build_container_ref = require_env("BUILD_CONTAINER_REF")
    default_akmods_ref = _resolve_default_akmods_ref()

    if use_input_lock:
        lock_data = _load_lock_file(lock_file_path)
        base_image_ref = str(lock_data.get("base_image") or "")
        lock_build_container_ref = str(lock_data.get("build_container") or "")
        zfs_minor_version = str(lock_data.get("zfs_minor_version") or "")
        akmods_upstream_ref = str(lock_data.get("akmods_upstream_ref") or "")

        if not base_image_ref:
            raise CiToolError("Lock file missing required field: base_image")
        if "REPLACE_ME" in base_image_ref:
            raise CiToolError("Lock file base_image still contains placeholder value")
        if lock_build_container_ref and "REPLACE_ME" in lock_build_container_ref:
            raise CiToolError("Lock file build_container still contains placeholder value")
        if lock_build_container_ref and build_container_ref != lock_build_container_ref:
            raise CiToolError(
                "Replay mismatch: build container input "
                f"({build_container_ref}) does not match lock file "
                f"({lock_build_container_ref}). Set workflow input "
                f"build_container_image={lock_build_container_ref} when use_input_lock=true."
            )

        if not zfs_minor_version:
            zfs_minor_version = require_env_or_default("DEFAULT_ZFS_MINOR_VERSION")
        if not akmods_upstream_ref:
            akmods_upstream_ref = default_akmods_ref
    else:
        base_image_ref = require_env_or_default("DEFAULT_BASE_IMAGE")
        zfs_minor_version = require_env_or_default("DEFAULT_ZFS_MINOR_VERSION")
        akmods_upstream_ref = default_akmods_ref

    return (
        use_input_lock,
        lock_file_path,
        build_container_ref,
        base_image_ref,
        zfs_minor_version,
        akmods_upstream_ref,
    )


def resolve_build_inputs() -> BuildInputResolution:
    """
    Resolve one complete set of build inputs from env and registry state.

    This is the core logic behind the main workflow and the non-main validation
    workflows. Sharing it here means every path pins the same base image, build
    container, Fedora version, and supported primary kernel.
    """

    (
        use_input_lock,
        lock_file_path,
        build_container_ref,
        base_image_ref,
        zfs_minor_version,
        akmods_upstream_ref,
    ) = resolve_configured_inputs()

    # Read base image metadata from registry.
    # Labels carry kernel information and stream version information.
    base_inspect_json = skopeo_inspect_json(f"docker://{base_image_ref}")
    base_image_name = str(base_inspect_json.get("Name") or "")
    base_image_digest = str(base_inspect_json.get("Digest") or "")
    labels = base_inspect_json.get("Labels") or {}
    label_kernel_release = str(labels.get("ostree.linux") or "")
    base_image_version_label = str(labels.get("org.opencontainers.image.version") or "")

    if not base_image_name or not base_image_digest:
        raise CiToolError(f"Failed to resolve base image digest for {base_image_ref}")
    if not label_kernel_release:
        raise CiToolError(f"Failed to read ostree.linux label from {base_image_ref}")

    base_image_pinned = f"{base_image_name}@{base_image_digest}"
    detected_kernel_releases = detect_base_image_kernel_releases(base_image_pinned)
    kernel_release = detected_kernel_releases[-1]
    fedora_version = extract_fedora_version(kernel_release)
    source_tag = extract_source_tag(base_image_ref)

    # Helper function:
    # input tag -> lookup digest in registry.
    # Return empty string when lookup fails so tag selection can continue.
    def lookup_digest(candidate_tag: str) -> str:
        candidate_ref = f"docker://{base_image_name}:{candidate_tag}"
        try:
            return skopeo_inspect_digest(candidate_ref)
        except CiToolError:
            return ""

    base_image_tag, candidate_tags = choose_base_image_tag(
        source_tag=source_tag,
        version_label=base_image_version_label,
        fedora_version=fedora_version,
        expected_digest=base_image_digest,
        digest_lookup=lookup_digest,
    )

    # Final safety check: chosen tag must still match the expected digest.
    selected_tag_digest = lookup_digest(base_image_tag)
    if selected_tag_digest != base_image_digest:
        raise CiToolError(
            f"Resolved tag {base_image_name}:{base_image_tag} does not match digest {base_image_digest}"
        )

    build_container_inspect = skopeo_inspect_json(f"docker://{build_container_ref}")
    build_container_name = str(build_container_inspect.get("Name") or "")
    build_container_digest = str(build_container_inspect.get("Digest") or "")

    if not build_container_name or not build_container_digest:
        raise CiToolError(f"Failed to resolve build container digest for {build_container_ref}")

    build_container_pinned = f"{build_container_name}@{build_container_digest}"

    return BuildInputResolution(
        inputs=ResolvedBuildInputs(
            version=fedora_version,
            kernel_release=kernel_release,
            detected_kernel_releases=tuple(detected_kernel_releases),
            base_image_ref=base_image_ref,
            base_image_name=base_image_name,
            base_image_tag=base_image_tag,
            base_image_pinned=base_image_pinned,
            base_image_digest=base_image_digest,
            build_container_ref=build_container_ref,
            build_container_pinned=build_container_pinned,
            build_container_digest=build_container_digest,
            zfs_minor_version=zfs_minor_version,
            akmods_upstream_ref=akmods_upstream_ref,
            use_input_lock=use_input_lock,
            lock_file_path=lock_file_path,
        ),
        label_kernel_release=label_kernel_release,
        candidate_tags=tuple(candidate_tags),
    )


def main() -> None:
    resolution = resolve_build_inputs()
    inputs = resolution.inputs

    # Export resolved values for downstream workflow steps.
    write_resolved_build_outputs(inputs)

    print(f"Resolved base image: {inputs.base_image_pinned}")
    print(f"Resolved base image tag: {inputs.base_image_name}:{inputs.base_image_tag}")
    print(f"Resolved build container: {inputs.build_container_pinned}")
    if resolution.label_kernel_release != inputs.kernel_release:
        print(
            "Base image label/kernel directory mismatch: "
            f"label={resolution.label_kernel_release} newest_dir={inputs.kernel_release}"
        )
    print(f"Supported primary kernel release: {inputs.kernel_release}")
    print(
        "Detected kernel releases in base image: "
        f"{' '.join(inputs.detected_kernel_releases)}"
    )
    print(f"Fedora version: {inputs.version}")
    print(f"ZFS minor version: {inputs.zfs_minor_version}")

    # Helpful for debugging: shows exactly which tags were considered.
    if resolution.candidate_tags:
        print(f"Base-tag candidates checked: {' '.join(resolution.candidate_tags)}")


if __name__ == "__main__":
    main()

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
Separately, a scheduled run builds when this repository's main branch has a
commit that changes the image and has never shipped. Merging to main starts a
push build, and a scheduled run that starts while that build is still running
cancels it (`concurrency` in build.yml). Without this check the scheduled run
would then skip, because neither upstream signal moved, and the merged change
would never reach `:latest`.
Goal: Fail closed on unknown upstream state and skip only truly unchanged
scheduled runs.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import time
import urllib.request
from dataclasses import dataclass

from ci_tools.common import (
    CiToolError,
    normalize_owner,
    optional_env,
    registry_creds_from_env,
    require_env,
    require_env_or_default,
    skopeo_inspect_json,
    skopeo_inspect_json_optional,
    write_github_outputs,
)
from ci_tools.promote_stable import STABLE_RE, list_repository_tags
from ci_tools.zfs_release import resolve_latest_zfs_version

STABLE_SIGNAL_IMAGE_LABEL = "org.zfs-aurora-complex.stable-signal-image"
STABLE_SIGNAL_DIGEST_LABEL = "org.zfs-aurora-complex.stable-signal-digest"
ZFS_VERSION_LABEL = "org.zfs-aurora-complex.zfs-version"

# A literal copy of `on.push.paths-ignore` in .github/workflows/build.yml. A
# push that changes only these paths starts no build, so a commit that changes
# only these paths has nothing to ship. GitHub reads that list as literal YAML,
# so it cannot be generated from here; tests/test_check_stable_signal.py keeps
# the two equal.
PUSH_PATHS_IGNORE = (
    "README.md",
    "**/README.md",
    "docs/**",
    "**/*.md",
)

GITHUB_API_URL = "https://api.github.com"
# The compare API returns at most this many files and silently drops the rest.
# A file list of this length may be incomplete, so it cannot prove that every
# change is in an ignored path.
COMPARE_FILES_CAP = 300
# How many `stable-*` audit tags, newest first, to inspect while looking for
# the one that shares `:latest`'s digest. Promotion writes the audit tag just
# before it moves `:latest`, so the newest one matches unless a promotion was
# cancelled between the two copies or `:latest` was moved by hand.
MAX_AUDIT_TAGS_INSPECTED = 10
# Seconds the audit-tag walk may spend before it gives up and reports the
# shipped commit as unknown, which builds. Each registry call is bounded by
# `REGISTRY_METADATA_TIMEOUT` (120s), so ten slow inspects alone could outlast
# the preflight job's `timeout-minutes: 10` and fail the run instead. The
# deadline is checked before each inspect, so the walk ends at most one call
# past it: 180s plus one 120s inspect plus the 30s compare stays near half the
# job limit.
AUDIT_TAG_WALK_DEADLINE = 180.0


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


def _github_glob_regex(pattern: str) -> re.Pattern[str]:
    """
    Translate one workflow path filter into a regular expression.

    GitHub's filter syntax is not `fnmatch`: `*` stops at `/`, `**` crosses
    it, and `**/` also matches no directory at all, so `**/*.md` matches a
    root `CHANGELOG.md` as well as `docs/a/b.md`. `fnmatch` lets `*` cross
    `/`, which would read `docs/*` as covering every nested file. Only the
    syntax `PUSH_PATHS_IGNORE` uses is translated; anything else is refused
    rather than guessed at, so adding a pattern with `[`, `!` or `+` to the
    list fails the tests instead of matching the wrong files.
    """
    out: list[str] = []
    index = 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            out.append("(?:.*/)?")
            index += 3
        elif pattern.startswith("**", index):
            out.append(".*")
            index += 2
        elif pattern[index] == "*":
            out.append("[^/]*")
            index += 1
        elif pattern[index] == "?":
            out.append("[^/]")
            index += 1
        elif pattern[index] in "[]!+":
            raise CiToolError(f"Unsupported path filter syntax in {pattern!r}")
        else:
            out.append(re.escape(pattern[index]))
            index += 1
    return re.compile("".join(out) + r"\Z")


def is_push_ignored_path(path: str) -> bool:
    """True when a push changing only `path` would start no build.yml run."""
    return any(_github_glob_regex(pattern).match(path) for pattern in PUSH_PATHS_IGNORE)


def find_shipped_commit(*, image_org: str, image_name: str, latest_digest: str, creds: str) -> str:
    """
    Return the short commit `:latest` was built from, or "" if it cannot be told.

    Promotion writes `stable-<run>-<sha7>` with the same digest just before it
    moves `:latest`, and `<sha7>` is that run's `GITHUB_SHA`. The image's own
    `org.opencontainers.image.revision` label is not usable: it is inherited
    from upstream Aurora and names Aurora's commit, not this repository's.

    Tags are inspected from the highest run number down, and the first one
    whose digest is `:latest`'s wins. Any registry error, or running past
    `AUDIT_TAG_WALK_DEADLINE`, returns "", which the caller turns into a build:
    not knowing what shipped is never read as "everything shipped".
    """
    if not latest_digest:
        return ""
    repository_ref = f"docker://ghcr.io/{image_org}/{image_name}"
    deadline = time.monotonic() + AUDIT_TAG_WALK_DEADLINE
    try:
        tags = list_repository_tags(repository_ref, creds=creds)
        audit_tags = sorted(
            (tag for tag in tags if STABLE_RE.match(tag)),
            key=lambda tag: int(tag.split("-")[1]),
            reverse=True,
        )
        for tag in audit_tags[:MAX_AUDIT_TAGS_INSPECTED]:
            if time.monotonic() >= deadline:
                print(
                    f"Gave up on the audit tags of {repository_ref} after "
                    f"{AUDIT_TAG_WALK_DEADLINE:.0f}s"
                )
                return ""
            inspect_json = skopeo_inspect_json(f"{repository_ref}:{tag}", creds=creds)
            if str(inspect_json.get("Digest") or "") == latest_digest:
                return tag.split("-")[2]
    except CiToolError as exc:
        print(f"Could not read the audit tags of {repository_ref}: {exc}")
    return ""


def fetch_commit_comparison(*, repository: str, base: str, head: str) -> dict:
    """
    Return the GitHub compare API's view of `base...head`.

    The preflight checkout is depth 1, so `git diff` would need a fetch first;
    the API needs only the job's `GITHUB_TOKEN`, which has `contents: read`.

    `http.client.HTTPException` is caught as well as `OSError`: a connection
    that drops while the body is read raises `IncompleteRead`, which is not an
    `OSError`, and an uncaught raise here would fail the scheduled run.
    """
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "zfs-aurora-complex",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN") or ""
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"{GITHUB_API_URL}/repos/{repository}/compare/{base}...{head}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
    except (OSError, ValueError, http.client.HTTPException) as exc:
        raise CiToolError(f"Failed to compare {base}...{head} in {repository}: {exc}") from exc
    if not isinstance(payload, dict):
        raise CiToolError(f"Unexpected compare payload for {base}...{head} in {repository}")
    return payload


def unshipped_commits_reason(
    *, repository: str, shipped_commit: str, head_sha: str
) -> str | None:
    """
    Return a build reason when main has an image change `:latest` lacks, else None.

    None means `:latest` was built from `head_sha`, or every file changed
    since is one a push would not have built for. Every case this function
    cannot settle returns a build reason instead of raising, because a raise
    here would fail the scheduled run and leave the change unshipped all the
    same. Building is the safe side: promotion still refuses to move `:latest`
    backwards.
    """
    if not shipped_commit:
        return "unshipped-commits-source-unknown"
    if head_sha.startswith(shipped_commit):
        return None
    try:
        comparison = fetch_commit_comparison(
            repository=repository, base=shipped_commit, head=head_sha
        )
    except CiToolError as exc:
        # A 404 lands here too, for example after a force-push removed the
        # shipped commit from the repository.
        print(f"Unshipped-commit check could not compare: {exc}")
        return "unshipped-commits-compare-failed"

    status = comparison.get("status")
    if status == "identical":
        return None
    if status != "ahead":
        # `behind` or `diverged`: main no longer contains what shipped.
        return "unshipped-commits-history-diverged"

    files = comparison.get("files")
    if not isinstance(files, list):
        return "unshipped-commits-compare-failed"
    for entry in files:
        if not isinstance(entry, dict) or not isinstance(entry.get("filename"), str):
            return "unshipped-commits-compare-failed"
        paths = [entry["filename"]]
        # A rename is a change on both sides: moving a file out of the image
        # tree changes the image as much as moving one in.
        if isinstance(entry.get("previous_filename"), str):
            paths.append(entry["previous_filename"])
        if any(not is_push_ignored_path(path) for path in paths):
            return "unshipped-commits"
    if len(files) >= COMPARE_FILES_CAP:
        return "unshipped-commits-file-list-truncated"
    return None


def evaluate_stable_signal_gate(
    *,
    image_org: str,
    image_name: str,
    stable_signal_image: str,
    zfs_minor_version: str,
    creds: str,
    repository: str,
    head_sha: str,
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

    Only when no upstream signal asks for a build does it look for a commit on
    main (`head_sha`, in `repository`) that changes the image and has not
    shipped. That check comes last so it can only add builds: every reason
    above still builds whatever it finds.
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

    if previous_signal_digest != stable_signal_digest:
        return StableSignalDecision(
            should_build=True,
            reason="stable-signal-advanced",
            stable_signal_ref=stable_signal_image,
            stable_signal_digest=stable_signal_digest,
            zfs_version=current_zfs_version,
        )

    shipped_commit = find_shipped_commit(
        image_org=image_org,
        image_name=image_name,
        latest_digest=str(current_latest_inspect.get("Digest") or ""),
        creds=creds,
    )
    unshipped_reason = unshipped_commits_reason(
        repository=repository, shipped_commit=shipped_commit, head_sha=head_sha
    )
    if unshipped_reason is not None:
        return StableSignalDecision(
            should_build=True,
            reason=unshipped_reason,
            stable_signal_ref=stable_signal_image,
            stable_signal_digest=stable_signal_digest,
            zfs_version=current_zfs_version,
        )

    return StableSignalDecision(
        should_build=False,
        reason="stable-signal-unchanged",
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
        creds = registry_creds_from_env(required=True)
        zfs_minor_version = require_env_or_default("DEFAULT_ZFS_MINOR_VERSION")

        decision = evaluate_stable_signal_gate(
            image_org=image_org,
            image_name=image_name,
            stable_signal_image=stable_signal_image,
            zfs_minor_version=zfs_minor_version,
            creds=creds,
            repository=require_env("GITHUB_REPOSITORY"),
            head_sha=require_env("GITHUB_SHA"),
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

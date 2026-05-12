"""
Script: ci_tools/classify_akmods_failure.py
What: Classifies an akmods/build failure log into a small set of known failure kinds.
Doing: Reads a log file, scans a tight allowlist of patterns, writes classification to GitHub outputs and a sticky-issue payload.
Why: Lets the sticky-issue workflow open one issue per distinct upstream-compat outage and auto-close on green without hiding red builds.
Goal: Make persistent upstream failures informative without suppressing anything.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Iterable

from ci_tools.common import optional_env, write_github_outputs


FAILURE_KIND_UPSTREAM_COMPAT = "upstream-compat"
FAILURE_KIND_UNKNOWN = "unknown"


ZFS_META_VERSION_RE = re.compile(r"ZFS_META_VERSION='([^']+)'")
ZFS_META_KVER_MAX_RE = re.compile(r"ZFS_META_KVER_MAX='([^']+)'")
KERNEL_MAJOR_MINOR_RE = re.compile(r"^(\d+)\.(\d+)")
ZFS_MAX_KERNEL_MISMATCH_PATTERN = "OpenZFS max supported kernel is below resolved kernel"


UPSTREAM_COMPAT_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Kernel API drift: kmod source calls a symbol the current kernel no longer provides
    re.compile(r"implicit declaration of function", re.IGNORECASE),
    re.compile(r"unknown type name '?struct\b", re.IGNORECASE),
    re.compile(r"has no member named", re.IGNORECASE),
    re.compile(r"error: incompatible (?:pointer )?type", re.IGNORECASE),
    re.compile(r"conflicting types for", re.IGNORECASE),
    re.compile(r"passing argument \d+ of .* from incompatible pointer type", re.IGNORECASE),
    # Kbuild / akmods build surface
    re.compile(r"error: kernel [^\s]+ not configured", re.IGNORECASE),
    re.compile(r"Badly formed kernel version", re.IGNORECASE),
    re.compile(r"kmod-zfs-[^\s]*\.rpm.*not found", re.IGNORECASE),
    re.compile(r"Cached akmods do not cover the supported kernel", re.IGNORECASE),
    # Our own install helper fail-closed path
    re.compile(r"No matching kmod-zfs RPM for kernel", re.IGNORECASE),
    # ZFS configure-time rejection of the running kernel
    re.compile(r"configure: error: unsupported kernel version", re.IGNORECASE),
    re.compile(r"configure: error: \*{3} Please use a newer kernel", re.IGNORECASE),
    re.compile(r"Unsupported Linux [0-9]", re.IGNORECASE),
)


def kernel_major_minor(value: str) -> tuple[int, int] | None:
    """Return a comparable kernel major/minor tuple from values like `7.0.4`."""
    match = KERNEL_MAJOR_MINOR_RE.search(value)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def zfs_metadata_from_log(log_text: str) -> tuple[str, str]:
    """Extract OpenZFS version and declared max kernel from configure output."""
    version_match = ZFS_META_VERSION_RE.search(log_text)
    max_match = ZFS_META_KVER_MAX_RE.search(log_text)
    return (version_match.group(1) if version_match else "", max_match.group(1) if max_match else "")


def zfs_max_kernel_is_below_resolved_kernel(log_text: str, kernel_release: str) -> bool:
    """Return true when OpenZFS metadata says the resolved kernel is too new."""
    _, max_kernel = zfs_metadata_from_log(log_text)
    resolved = kernel_major_minor(kernel_release)
    supported_max = kernel_major_minor(max_kernel)
    if resolved is None or supported_max is None:
        return False
    return resolved > supported_max


def build_failure_summary(*, failure_kind: str, kernel_release: str, log_text: str) -> str:
    """Build the human-readable reason shown in artifacts and job summaries."""
    zfs_version, max_kernel = zfs_metadata_from_log(log_text)
    if failure_kind == FAILURE_KIND_UPSTREAM_COMPAT and max_kernel:
        version = zfs_version or "the selected OpenZFS release"
        return (
            f"OpenZFS {version} supports Linux kernels up to {max_kernel}, "
            f"but the resolved base image uses {kernel_release}. "
            "The build is intentionally failing closed and image promotion was skipped."
        )
    if failure_kind == FAILURE_KIND_UPSTREAM_COMPAT:
        return (
            "The akmods build failed with a known upstream ZFS/kernel compatibility pattern. "
            "The build is intentionally failing closed and image promotion was skipped."
        )
    return "The akmods build failed, but no known compatibility pattern matched the log."


def classify_log_text(log_text: str, *, kernel_release: str = "") -> tuple[str, list[str]]:
    """
    Classify a failure log body.

    Returns:
    - failure_kind: one of the FAILURE_KIND_* constants
    - matched_patterns: every upstream-compat pattern whose regex found at least
      one match in the log body, preserving the declaration order of
      `UPSTREAM_COMPAT_PATTERNS`
    """
    if not log_text:
        return FAILURE_KIND_UNKNOWN, []

    matched: list[str] = []
    for pattern in UPSTREAM_COMPAT_PATTERNS:
        if pattern.search(log_text):
            matched.append(pattern.pattern)

    if zfs_max_kernel_is_below_resolved_kernel(log_text, kernel_release):
        matched.append(ZFS_MAX_KERNEL_MISMATCH_PATTERN)

    if matched:
        return FAILURE_KIND_UPSTREAM_COMPAT, matched
    return FAILURE_KIND_UNKNOWN, []


def build_sticky_issue_payload(
    *,
    failure_kind: str,
    kernel_release: str,
    akmods_upstream_ref: str,
    fedora_version: str,
    run_id: str,
    run_url: str,
    matched_patterns: Iterable[str],
    summary: str = "",
) -> dict:
    """
    Build the sticky-issue payload the visibility workflow uploads as an artifact.

    Key fields:
    - `key`: identifies the distinct failure so one issue tracks one outage
    - `title`: human-readable title the sticky-issue workflow uses verbatim
    - `body`: pre-rendered markdown body
    """
    safe_ref = (akmods_upstream_ref or "unknown-ref")[:12]
    key = f"{failure_kind}:{kernel_release or 'unknown-kernel'}:{safe_ref}"
    if failure_kind == FAILURE_KIND_UPSTREAM_COMPAT:
        title = f"Upstream ZFS/kernel incompatibility: {kernel_release} + akmods@{safe_ref}"
    else:
        title = f"Unclassified akmods build failure: {kernel_release} + akmods@{safe_ref}"

    matched_list = "\n".join(f"- `{pat}`" for pat in matched_patterns) or "- (none)"
    summary_section = f"**Summary:** {summary}\n\n" if summary else ""
    body = (
        f"**Failure kind:** `{failure_kind}`\n\n"
        f"{summary_section}"
        f"**Primary kernel:** `{kernel_release}`\n"
        f"**Akmods upstream ref:** `{akmods_upstream_ref}`\n"
        f"**Fedora version:** `{fedora_version}`\n\n"
        f"**Matched patterns:**\n{matched_list}\n\n"
        f"**Failing run:** [{run_id}]({run_url})\n\n"
        "This issue was opened automatically by the visibility workflow. "
        "It will auto-close on the next green `build.yml` run."
    )

    return {
        "key": key,
        "title": title,
        "body": body,
        "failure_kind": failure_kind,
        "kernel_release": kernel_release,
        "akmods_upstream_ref": akmods_upstream_ref,
        "fedora_version": fedora_version,
        "run_id": run_id,
        "run_url": run_url,
        "summary": summary,
    }


def build_step_summary_markdown(payload: dict) -> str:
    """Render a short GitHub Actions step summary for the failed akmods build."""
    lines = [
        "## Akmods build failure",
        "",
        f"- Failure kind: `{payload['failure_kind']}`",
        f"- Primary kernel: `{payload['kernel_release']}`",
        f"- Fedora version: `{payload['fedora_version']}`",
        f"- Akmods upstream ref: `{payload['akmods_upstream_ref']}`",
    ]
    if payload.get("summary"):
        lines.extend(["", payload["summary"]])
    if payload.get("run_url"):
        lines.extend(["", f"Failing run: {payload['run_url']}"])
    return "\n".join(lines) + "\n"


def write_step_summary(payload: dict) -> None:
    """Append the akmods failure explanation to GitHub's job summary when available."""
    summary_path = optional_env("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    with Path(summary_path).open("a", encoding="utf-8") as summary_file:
        summary_file.write(build_step_summary_markdown(payload))


def main() -> None:
    log_path = optional_env("AKMODS_FAILURE_LOG")
    kernel_release = optional_env("KERNEL_RELEASE")
    akmods_upstream_ref = optional_env("AKMODS_UPSTREAM_REF")
    fedora_version = optional_env("FEDORA_VERSION")
    run_id = optional_env("GITHUB_RUN_ID")
    server_url = optional_env("GITHUB_SERVER_URL", "https://github.com")
    repository = optional_env("GITHUB_REPOSITORY")
    run_url = f"{server_url}/{repository}/actions/runs/{run_id}" if repository and run_id else ""
    payload_out = optional_env("AKMODS_FAILURE_PAYLOAD_PATH") or "artifacts/akmods-failure.json"

    log_text = ""
    if log_path and Path(log_path).exists():
        log_text = Path(log_path).read_text(encoding="utf-8", errors="replace")

    failure_kind, matched_patterns = classify_log_text(log_text, kernel_release=kernel_release)
    summary = build_failure_summary(
        failure_kind=failure_kind,
        kernel_release=kernel_release,
        log_text=log_text,
    )

    payload = build_sticky_issue_payload(
        failure_kind=failure_kind,
        kernel_release=kernel_release,
        akmods_upstream_ref=akmods_upstream_ref,
        fedora_version=fedora_version,
        run_id=run_id,
        run_url=run_url,
        matched_patterns=matched_patterns,
        summary=summary,
    )

    out_path = Path(payload_out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    write_step_summary(payload)

    if os.environ.get("GITHUB_OUTPUT"):
        write_github_outputs(
            {
                "failure_kind": failure_kind,
                "sticky_key": payload["key"],
                "payload_path": str(out_path),
            }
        )

    print(f"Classified akmods failure as {failure_kind}. Payload written to {out_path}.")


if __name__ == "__main__":
    main()

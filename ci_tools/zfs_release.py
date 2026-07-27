"""
Script: ci_tools/zfs_release.py
What: Resolves the newest published OpenZFS release on one configured minor line.
Doing: Queries the OpenZFS GitHub releases API and picks the highest patch number
whose (major, minor) matches the requested line exactly.
Why: OpenZFS ships maintenance releases for several minor lines in one batch (for
example zfs-2.4.3, zfs-2.3.8, and zfs-2.2.10 have been published within seconds
of each other). Trusting API result order, or matching by string prefix across
lines, can select an older line's release that merely happened to publish
later -- silently downgrading ZFS. Filtering to the exact requested line before
comparing patch numbers makes that impossible.
Goal: Give both the scheduled-build gate and the akmods cache-reuse check one
trustworthy answer to "what is the newest release on this line right now".
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from collections.abc import Callable

from ci_tools.common import CiToolError

OPENZFS_RELEASES_URL = "https://api.github.com/repos/openzfs/zfs/releases"
MINOR_VERSION_RE = re.compile(r"^(\d+)\.(\d+)$")
RELEASE_TAG_RE = re.compile(r"^zfs-(\d+)\.(\d+)\.(\d+)$")

# The API caps `per_page` at 100. Five pages covers OpenZFS's entire release
# history several times over, and bounds this to at most five requests.
RELEASES_PER_PAGE = 100
MAX_RELEASE_PAGES = 5


def fetch_openzfs_releases() -> list[dict]:
    """
    Fetch the OpenZFS releases list from the GitHub API.

    Paginates rather than reading only the API's default first page. One page
    is 30 releases, and OpenZFS publishes maintenance releases for several
    minor lines at once, so a line that is not the newest can have its most
    recent release pushed past a single page by newer releases on other lines.
    Reading one page would then either find nothing for that line or -- worse --
    silently settle for an older patch that happened to fall inside the window.

    Sends the GitHub token when one is available. Unauthenticated calls to
    api.github.com are limited to 60 per hour *per IP address*, and hosted
    runners share IPs, so an unauthenticated call here can fail for reasons
    that have nothing to do with this repository. Authenticated calls get a
    far higher per-repository limit. The token is optional: local runs without
    one still work, they just use the lower shared limit.
    """
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "zfs-aurora-complex",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    releases: list[dict] = []
    for page in range(1, MAX_RELEASE_PAGES + 1):
        url = f"{OPENZFS_RELEASES_URL}?per_page={RELEASES_PER_PAGE}&page={page}"
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                page_items = json.loads(response.read())
        except (OSError, ValueError) as exc:
            raise CiToolError(f"Failed to fetch OpenZFS releases from {url}: {exc}") from exc

        if not isinstance(page_items, list):
            raise CiToolError(f"Unexpected OpenZFS releases payload from {url}")

        releases.extend(page_items)
        # A short page is the last page.
        if len(page_items) < RELEASES_PER_PAGE:
            break

    return releases


def resolve_latest_zfs_version(
    minor_version: str,
    *,
    releases_fetcher: Callable[[], list[dict]] = fetch_openzfs_releases,
) -> str:
    """
    Return the newest non-draft, non-prerelease patch version on one ZFS minor line.

    `minor_version` must be exactly `<major>.<minor>` (for example `2.4`). Every
    candidate release is required to match that (major, minor) pair before its
    patch number is even considered, so this can never return a version from a
    different line -- there is no cross-line comparison for a later publish
    timestamp to win.
    """
    minor_match = MINOR_VERSION_RE.match(minor_version)
    if not minor_match:
        raise CiToolError(f"Invalid ZFS minor version: {minor_version!r}")
    minor_key = (int(minor_match.group(1)), int(minor_match.group(2)))

    best_patch: int | None = None
    for release in releases_fetcher():
        if release.get("draft") or release.get("prerelease"):
            continue
        tag_match = RELEASE_TAG_RE.match(str(release.get("tag_name") or ""))
        if not tag_match:
            continue
        release_key = (int(tag_match.group(1)), int(tag_match.group(2)))
        if release_key != minor_key:
            continue
        patch = int(tag_match.group(3))
        if best_patch is None or patch > best_patch:
            best_patch = patch

    if best_patch is None:
        raise CiToolError(f"No published OpenZFS release found for minor line {minor_version}")

    return f"{minor_key[0]}.{minor_key[1]}.{best_patch}"

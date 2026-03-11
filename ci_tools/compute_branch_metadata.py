"""
Script: ci_tools/compute_branch_metadata.py
What: Creates branch-safe tag text from the branch name.
Doing: Lowercases the name, replaces unsupported characters, limits length, and writes outputs.
Why: Prevents invalid tag names and accidental tag collisions.
Goal: Provide safe branch image tags for branch builds.
"""

from __future__ import annotations

import re

from ci_tools.common import require_env, write_github_outputs


UNSAFE_CHARS_RE = re.compile(r"[^a-z0-9._-]+")
MAX_LENGTH = 120


def sanitize_branch_name(branch: str) -> str:
    """Convert a branch name into a registry-safe identifier."""

    safe = UNSAFE_CHARS_RE.sub("-", branch.lower()).strip("-")
    return safe or "branch"


def clamp_tag(value: str, fallback: str) -> str:
    """Truncate and clean a tag string while preserving a fallback."""

    trimmed = value[:MAX_LENGTH].rstrip("-")
    return trimmed or fallback


def build_branch_metadata(branch_name: str) -> str:
    """Return one branch-scoped image tag prefix like `br-my-branch`."""

    safe_branch = sanitize_branch_name(branch_name)
    return clamp_tag(f"br-{safe_branch}", "br-branch")


def main() -> None:
    branch_name = require_env("GITHUB_REF_NAME")
    branch_tag = build_branch_metadata(branch_name)
    write_github_outputs({"branch_tag": branch_tag})
    print(f"Branch image tag prefix: {branch_tag}")


if __name__ == "__main__":
    main()

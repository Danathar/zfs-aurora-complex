"""
Script: ci_tools/akmods_clone_pinned.py
What: Clones the exact akmods commit resolved for this run into `/tmp/akmods`.
Doing: Recreates the directory, fetches one commit, checks that Git is pointed at the fetched commit itself instead of a moving branch tip, and verifies the commit SHA.
Why: Ensures we build from a known source version without mutating the cloned worktree at runtime.
Goal: Prepare a clean akmods checkout for later configure/build steps.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ci_tools.common import CiToolError, require_env_or_default, run_cmd


AKMODS_WORKTREE = Path("/tmp/akmods")


def clone_pinned(upstream_repo: str, upstream_ref: str) -> None:
    """
    Clone exactly `upstream_ref` from `upstream_repo` into AKMODS_WORKTREE.

    Callers pass the already-resolved repo URL and commit SHA so this step
    never re-reads env and cannot silently pick up a different value.
    """

    if not upstream_repo:
        raise CiToolError("clone_pinned requires a non-empty upstream_repo")
    if not upstream_ref:
        raise CiToolError("clone_pinned requires a non-empty upstream_ref")

    # Start from a clean checkout each run so there is no leftover state from an
    # earlier build. The later configure/build helpers expect `/tmp/akmods` to
    # reflect exactly one resolved commit.
    shutil.rmtree(AKMODS_WORKTREE, ignore_errors=True)
    AKMODS_WORKTREE.mkdir(parents=True, exist_ok=True)

    # Create a minimal local repository and fetch only the one commit we need.
    # Checking out the fetched commit directly keeps this step deterministic and fast.
    run_cmd(["git", "init", "."], cwd=str(AKMODS_WORKTREE))
    run_cmd(["git", "remote", "add", "origin", upstream_repo], cwd=str(AKMODS_WORKTREE))
    run_cmd(["git", "fetch", "--depth", "1", "origin", upstream_ref], cwd=str(AKMODS_WORKTREE))
    run_cmd(["git", "checkout", "--detach", "FETCH_HEAD"], cwd=str(AKMODS_WORKTREE))

    # Extra safety check: fail if Git resolved to anything other than the exact
    # commit SHA the workflow asked for.
    resolved_ref = run_cmd(["git", "rev-parse", "HEAD"], cwd=str(AKMODS_WORKTREE)).strip()
    if resolved_ref != upstream_ref:
        raise CiToolError(f"Akmods ref mismatch: expected {upstream_ref}, got {resolved_ref}")

    print(f"Using resolved akmods ref: {resolved_ref}")


def main() -> None:
    # The repo and SHA live in checked-in defaults, but workflow env can still
    # override them for one-off testing. That keeps the normal path reviewable
    # while preserving an escape hatch for manual validation.
    upstream_repo = require_env_or_default("AKMODS_UPSTREAM_REPO")
    upstream_ref = require_env_or_default("AKMODS_UPSTREAM_REF")
    clone_pinned(upstream_repo, upstream_ref)


if __name__ == "__main__":
    main()

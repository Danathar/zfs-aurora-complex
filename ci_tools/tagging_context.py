"""
Script: ci_tools/tagging_context.py
What: Shared helpers for lightweight tag naming and registry-context exports.
Doing: Builds candidate tags, branch tags, branch-safe prefixes, and registry
context values, then exposes small command entrypoints for workflow use.
Why: These rules are simple, but several workflows depend on them. Keeping them
in one small module avoids scattering one-line data shaping across many files.
Goal: Reduce tiny helper-file count without pushing this logic back into YAML.
"""

from __future__ import annotations

import re

from ci_tools.common import normalize_owner, require_env, write_github_env, write_github_outputs

UNSAFE_CHARS_RE = re.compile(r"[^a-z0-9._-]+")
MAX_LENGTH = 120


def build_candidate_tag(*, github_sha: str, fedora_version: str) -> str:
    """Return one candidate tag like `candidate-deadbee-43`."""

    return f"candidate-{github_sha[:7]}-{fedora_version}"


def build_branch_image_tag(*, branch_tag_prefix: str, fedora_version: str) -> str:
    """Return one branch image tag like `br-my-branch-43`."""

    return f"{branch_tag_prefix}-{fedora_version}"


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


def actor_is_bot(actor_name: str) -> bool:
    """True when the current GitHub account name follows the bot naming pattern."""

    return "[bot]" in actor_name


def export_registry_context_values(*, repository_owner: str, actor_name: str) -> dict[str, str]:
    """
    Return normalized registry context values for workflow steps.

    Output values are shaped exactly like the current workflow env/output names
    so the rest of the pipeline does not need to care where the values came
    from.
    """

    image_org = normalize_owner(repository_owner)
    image_registry = f"ghcr.io/{image_org}"
    is_bot = "true" if actor_is_bot(actor_name) else "false"
    return {
        "image_org": image_org,
        "image_registry": image_registry,
        "actor_is_bot": is_bot,
    }


def main_compute_candidate_tag() -> None:
    candidate_tag = build_candidate_tag(
        github_sha=require_env("GITHUB_SHA"),
        fedora_version=require_env("FEDORA_VERSION"),
    )
    write_github_outputs({"candidate_tag": candidate_tag})
    print(f"Candidate image tag: {candidate_tag}")


def main_compose_branch_image_tag() -> None:
    branch_image_tag = build_branch_image_tag(
        branch_tag_prefix=require_env("BRANCH_TAG_PREFIX"),
        fedora_version=require_env("FEDORA_VERSION"),
    )
    write_github_outputs({"branch_image_tag": branch_image_tag})
    print(f"Branch image tag: {branch_image_tag}")


def main_compute_branch_metadata() -> None:
    branch_tag = build_branch_metadata(require_env("GITHUB_REF_NAME"))
    write_github_outputs({"branch_tag": branch_tag})
    print(f"Branch image tag prefix: {branch_tag}")


def main_export_registry_context() -> None:
    values = export_registry_context_values(
        repository_owner=require_env("GITHUB_REPOSITORY_OWNER"),
        actor_name=require_env("GITHUB_ACTOR"),
    )
    write_github_env(
        {
            "IMAGE_ORG": values["image_org"],
            "IMAGE_REGISTRY": values["image_registry"],
            "ACTOR_IS_BOT": values["actor_is_bot"],
        }
    )
    write_github_outputs(values)
    print(
        "Prepared registry context: "
        f"image_org={values['image_org']} "
        f"image_registry={values['image_registry']} "
        f"actor_is_bot={values['actor_is_bot']}"
    )

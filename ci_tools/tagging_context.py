"""
Script: ci_tools/tagging_context.py
What: Shared helpers for lightweight tag naming and registry-context exports.
Doing: Builds candidate tags and registry context values, then exposes small
command entrypoints for workflow use.
Why: These rules are simple, but several workflows depend on them. Keeping them
in one small module avoids scattering one-line data shaping across many files.
Goal: Reduce tiny helper-file count without pushing this logic back into YAML.
"""

from __future__ import annotations

from ci_tools.common import normalize_owner, require_env, write_github_env, write_github_outputs


def build_candidate_tag(*, github_sha: str, fedora_version: str) -> str:
    """Return one candidate tag like `candidate-deadbee-43`."""

    return f"candidate-{github_sha[:7]}-{fedora_version}"


def export_registry_context_values(*, repository_owner: str) -> dict[str, str]:
    """
    Return normalized registry context values for workflow steps.

    Output values are shaped exactly like the current workflow env/output names
    so the rest of the pipeline does not need to care where the values came
    from.
    """

    image_org = normalize_owner(repository_owner)
    image_registry = f"ghcr.io/{image_org}"
    return {
        "image_org": image_org,
        "image_registry": image_registry,
    }


def main_compute_candidate_tag() -> None:
    candidate_tag = build_candidate_tag(
        github_sha=require_env("GITHUB_SHA"),
        fedora_version=require_env("FEDORA_VERSION"),
    )
    write_github_outputs({"candidate_tag": candidate_tag})
    print(f"Candidate image tag: {candidate_tag}")


def main_export_registry_context() -> None:
    values = export_registry_context_values(
        repository_owner=require_env("GITHUB_REPOSITORY_OWNER"),
    )
    write_github_env(
        {
            "IMAGE_ORG": values["image_org"],
            "IMAGE_REGISTRY": values["image_registry"],
        }
    )
    write_github_outputs(values)
    print(
        "Prepared registry context: "
        f"image_org={values['image_org']} "
        f"image_registry={values['image_registry']}"
    )

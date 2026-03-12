"""
Script: ci_tools/cli.py
What: Command entrypoint used by workflow steps.
Doing: Maps command text to module `main()` functions and runs the selected command.
Why: Workflows can call one simple command pattern.
Goal: Keep step-to-code mapping clear and easy to maintain.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Mapping

from ci_tools.common import CiToolError


def command_map() -> dict[str, Callable[[], None]]:
    """
    Map CLI command names to Python entry functions.

    Each value is a `main()` function from one workflow helper module.
    """

    from ci_tools.akmods_build_and_publish import main as akmods_build_and_publish
    from ci_tools.akmods_clone_pinned import main as akmods_clone_pinned
    from ci_tools.akmods_configure_zfs_target import main as akmods_configure_zfs_target
    from ci_tools.check_akmods_cache import main as check_akmods_cache
    from ci_tools.compose_branch_image_tag import main as compose_branch_image_tag
    from ci_tools.compute_candidate_tag import main as compute_candidate_tag
    from ci_tools.compute_branch_metadata import main as compute_branch_metadata
    from ci_tools.export_repo_defaults import main as export_repo_defaults
    from ci_tools.export_registry_context import main as export_registry_context
    from ci_tools.prepare_validation_build import main as prepare_validation_build
    from ci_tools.promote_stable import main as promote_stable
    from ci_tools.resolve_build_inputs import main as resolve_build_inputs
    from ci_tools.sign_image import main as sign_image
    from ci_tools.write_build_inputs_manifest import main as write_build_inputs_manifest

    return {
        "resolve-build-inputs": resolve_build_inputs,
        "write-build-inputs-manifest": write_build_inputs_manifest,
        "check-akmods-cache": check_akmods_cache,
        "compose-branch-image-tag": compose_branch_image_tag,
        "compute-candidate-tag": compute_candidate_tag,
        "export-repo-defaults": export_repo_defaults,
        "export-registry-context": export_registry_context,
        "prepare-validation-build": prepare_validation_build,
        "compute-branch-metadata": compute_branch_metadata,
        "promote-stable": promote_stable,
        "sign-image": sign_image,
        "akmods-clone-pinned": akmods_clone_pinned,
        "akmods-configure-zfs-target": akmods_configure_zfs_target,
        "akmods-build-and-publish": akmods_build_and_publish,
    }


def build_parser(commands: Mapping[str, Callable[[], None]]) -> argparse.ArgumentParser:
    """Build argument parser with one positional command choice."""

    parser = argparse.ArgumentParser(
        prog="python3 -m ci_tools.cli",
        description="Run one workflow helper command.",
    )
    parser.add_argument("command", choices=sorted(commands.keys()))
    return parser


def run_command(command: str, commands: Mapping[str, Callable[[], None]]) -> None:
    """
    Run one registered command.

    `commands` is passed in to keep this function easy to test.
    """

    commands[command]()


def main(argv: list[str] | None = None) -> None:
    commands = command_map()
    parser = build_parser(commands)
    args = parser.parse_args(argv)

    try:
        run_command(args.command, commands)
    except CiToolError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()

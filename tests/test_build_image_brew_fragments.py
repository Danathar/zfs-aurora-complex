"""
Script: tests/test_build_image_brew_fragments.py
What: Tests that the brew login-shell fragments the Aurora base ships never reach the image, and that the fragment replacing them executes nothing.
Doing: Reads the Containerfile and build-image.sh, extracts the build-time sweep and runs it against fixture trees, and asserts the text of files/etc/profile.d/brew-path.sh.
Why: The aurora-dx base image carries the `ublue-os/brew` payload's files, which this repository does not write, and `brew-setup.service` leaves the prefix they execute from owned by UID 1000, so a login shell that sources them runs user-writable code as root.
Goal: Keep the removal, the sweep that backstops it, and the replacement fragment from quietly drifting apart.

The fragments are not in this tree at any revision, so nothing here can assert
their absence by reading a tracked file. What the suite can pin is the three
things the image build does about them: the removal, the sweep that fails the
build if the base image grows a fourth, and the one fragment that is allowed to
stay. The sweep is extracted and executed rather than pattern-matched, because
the property under test is what it does to a directory tree.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BUILD_IMAGE = REPO_ROOT / "build_files" / "build-image.sh"
CONTAINERFILE = REPO_ROOT / "Containerfile"
BREW_PATH_FRAGMENT = REPO_ROOT / "files" / "etc" / "profile.d" / "brew-path.sh"

# Every login-shell fragment the ublue-os/brew payload in the aurora-dx base
# ships. Read out of the published image itself (#284), not guessed from the
# upstream repository's default branch.
PAYLOAD_FRAGMENTS = (
    "/etc/profile.d/brew.sh",
    "/etc/profile.d/brew-bash-completion.sh",
    "/usr/share/fish/vendor_conf.d/ublue-brew.fish",
)

SWEEP_FUNCTION = "check_brew_login_fragments"

# Matches the sweep function from its opening line to the closing brace in
# column 1. build-image.sh defines no nested functions, so this is unambiguous.
SWEEP_RE = re.compile(
    rf"^{SWEEP_FUNCTION}\(\) \{{\n.*?^\}}\n",
    re.MULTILINE | re.DOTALL,
)


def build_image_text() -> str:
    return BUILD_IMAGE.read_text(encoding="utf-8")


def removed_paths() -> tuple[str, ...]:
    """Return the paths the build's `rm -f` block deletes, in order."""

    match = re.search(r"^rm -f \\\n((?:  /\S+ ?\\?\n)+)", build_image_text(), re.MULTILINE)
    assert match is not None, f"no `rm -f` block removing the brew fragments in {BUILD_IMAGE}"
    return tuple(line.strip().rstrip(" \\") for line in match.group(1).splitlines())


def sweep_source() -> str:
    """Return the sweep function exactly as build-image.sh defines it."""

    match = SWEEP_RE.search(build_image_text())
    assert match is not None, f"{SWEEP_FUNCTION} not found in {BUILD_IMAGE}"
    return match.group(0)


def run_sweep(root: Path) -> subprocess.CompletedProcess[str]:
    """Run the real sweep body against a fixture tree rooted at `root`."""

    script = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"{sweep_source()}\n"
        f'{SWEEP_FUNCTION} "$1"\n'
    )
    with tempfile.NamedTemporaryFile("w", suffix=".sh", encoding="utf-8") as handle:
        handle.write(script)
        handle.flush()
        return subprocess.run(
            ["bash", handle.name, str(root)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )


def fixture_tree(root: Path, files: dict[str, str]) -> None:
    for relative, content in files.items():
        path = root / relative.lstrip("/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


ALLOWED_ONLY = {"/etc/profile.d/brew-path.sh": "# brew prefix on PATH\n"}


class PremiseTests(unittest.TestCase):
    """The premise the rest of this file rests on."""

    def test_the_brew_payload_still_arrives_inside_the_aurora_base(self) -> None:
        # Here the fragments come with the aurora-dx base image, not from a
        # COPY this repository controls. If the base ever changes to one without
        # brew, the removal below becomes dead weight and this suite should be
        # revisited rather than left asserting something that protects nothing.
        self.assertIn(
            'ARG BASE_IMAGE="ghcr.io/ublue-os/aurora-dx:stable"',
            CONTAINERFILE.read_text(encoding="utf-8"),
        )


class RemovalTests(unittest.TestCase):
    def test_every_payload_fragment_is_removed(self) -> None:
        removed = removed_paths()
        for fragment in PAYLOAD_FRAGMENTS:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, removed)

    def test_the_removal_is_a_single_rm_of_exactly_those_paths(self) -> None:
        # Exactly, not merely: a fourth path here would be a decision about the
        # base image that belongs in the comment above it and in review.
        self.assertEqual(removed_paths(), PAYLOAD_FRAGMENTS)

    def test_nothing_sources_or_evals_the_prefix_anywhere_in_the_build(self) -> None:
        # Comments stripped: the block above names these on purpose, in prose.
        code = "\n".join(
            line for line in build_image_text().splitlines() if not line.lstrip().startswith("#")
        )
        self.assertNotIn("brew shellenv", code)
        self.assertNotIn("/home/linuxbrew/.linuxbrew/bin/brew", code)


class SweepTests(unittest.TestCase):
    def test_a_tree_holding_only_the_allowed_fragment_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fixture_tree(root, ALLOWED_ONLY)
            result = run_sweep(root)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_reintroduced_profile_fragment_fails_the_build(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fixture_tree(
                root,
                {
                    **ALLOWED_ONLY,
                    "/etc/profile.d/brew.sh": 'eval "$(brew shellenv)"\n',
                },
            )
            result = run_sweep(root)
            self.assertEqual(result.returncode, 1)
            self.assertIn("/etc/profile.d/brew.sh", result.stderr)

    def test_a_reintroduced_fish_fragment_fails_the_build(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fixture_tree(
                root,
                {
                    **ALLOWED_ONLY,
                    "/usr/share/fish/vendor_conf.d/ublue-brew.fish": (
                        "brew shellenv fish | source\n"
                    ),
                },
            )
            result = run_sweep(root)
            self.assertEqual(result.returncode, 1)
            self.assertIn("ublue-brew.fish", result.stderr)

    def test_a_fragment_under_etc_fish_conf_d_fails_the_build(self) -> None:
        # /etc/fish/conf.d does not exist on the built image today. It is swept
        # anyway because the base image decides what it ships, not this repository.
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fixture_tree(
                root,
                {**ALLOWED_ONLY, "/etc/fish/conf.d/brew.fish": "brew shellenv fish | source\n"},
            )
            result = run_sweep(root)
            self.assertEqual(result.returncode, 1)
            self.assertIn("/etc/fish/conf.d/brew.fish", result.stderr)

    def test_an_unrelated_login_fragment_is_left_alone(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fixture_tree(
                root,
                {**ALLOWED_ONLY, "/etc/profile.d/colorls.sh": "alias ls='ls --color=auto'\n"},
            )
            result = run_sweep(root)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_login_shell_directories_are_not_an_error(self) -> None:
        # A `grep` over a directory that does not exist must not be read as
        # "clean" by accident and must not fail the build either: the sweep is
        # about what the base image landed, and a tree with no /etc/fish at all is
        # the normal case.
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fixture_tree(root, ALLOWED_ONLY)
            result = run_sweep(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((root / "etc" / "fish").exists())

    def test_the_sweep_actually_runs_in_the_build(self) -> None:
        # An extracted-and-tested function that build-image.sh never calls would
        # pass every test above and protect nothing.
        called = re.search(rf"^{SWEEP_FUNCTION}$", build_image_text(), re.MULTILINE)
        self.assertIsNotNone(called, f"{SWEEP_FUNCTION} is defined but never called")


class ReplacementFragmentTests(unittest.TestCase):
    def test_the_fragment_is_installed_by_the_build(self) -> None:
        self.assertIn(
            "/ctx/files/etc/profile.d/brew-path.sh",
            build_image_text(),
        )

    def test_the_fragment_executes_nothing(self) -> None:
        text = BREW_PATH_FRAGMENT.read_text(encoding="utf-8")
        body = "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("#")
        )
        for forbidden in ("$(", "`", "eval", "source ", "shellenv"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, body)

    def test_the_fragment_is_gated_on_owning_the_prefix(self) -> None:
        body = BREW_PATH_FRAGMENT.read_text(encoding="utf-8")
        self.assertIn("[ -O /home/linuxbrew/.linuxbrew ]", body)

    def test_the_prefix_is_appended_so_system_binaries_keep_priority(self) -> None:
        body = BREW_PATH_FRAGMENT.read_text(encoding="utf-8")
        self.assertIn(
            'PATH="${PATH}:/home/linuxbrew/.linuxbrew/bin:/home/linuxbrew/.linuxbrew/sbin"',
            body,
        )

    def test_the_fragment_adds_the_prefix_for_its_owner(self) -> None:
        # The point of keeping a fragment at all: `brew` on PATH for the account
        # that owns the prefix. Run the real file with that account being us.
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "home" / "linuxbrew" / ".linuxbrew" / "bin").mkdir(parents=True)
            rewritten = BREW_PATH_FRAGMENT.read_text(encoding="utf-8").replace(
                "/home/linuxbrew/.linuxbrew", str(root / "home/linuxbrew/.linuxbrew")
            )
            result = subprocess.run(
                ["bash", "-c", f'PATH=/usr/bin\n{rewritten}\necho "${{PATH}}"'],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout.strip(),
                f"/usr/bin:{root}/home/linuxbrew/.linuxbrew/bin"
                f":{root}/home/linuxbrew/.linuxbrew/sbin",
            )

    def test_the_fragment_is_inert_when_the_prefix_is_owned_by_someone_else(self) -> None:
        # Stand in for "owned by UID 1000, read by root": a prefix the running
        # account does not own must leave PATH untouched.
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "home" / "linuxbrew" / ".linuxbrew" / "bin").mkdir(parents=True)
            rewritten = BREW_PATH_FRAGMENT.read_text(encoding="utf-8").replace(
                "[ -O /home/linuxbrew/.linuxbrew ]", "[ -O /nonexistent-owner-check ]"
            ).replace(
                "/home/linuxbrew/.linuxbrew", str(root / "home/linuxbrew/.linuxbrew")
            )
            result = subprocess.run(
                ["bash", "-c", f'PATH=/usr/bin\n{rewritten}\necho "${{PATH}}"'],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "/usr/bin")

    def test_the_fragment_does_not_duplicate_the_prefix_on_a_second_login(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "home" / "linuxbrew" / ".linuxbrew" / "bin").mkdir(parents=True)
            rewritten = BREW_PATH_FRAGMENT.read_text(encoding="utf-8").replace(
                "/home/linuxbrew/.linuxbrew", str(root / "home/linuxbrew/.linuxbrew")
            )
            result = subprocess.run(
                ["bash", "-c", f'PATH=/usr/bin\n{rewritten}\n{rewritten}\necho "${{PATH}}"'],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout.strip(),
                f"/usr/bin:{root}/home/linuxbrew/.linuxbrew/bin"
                f":{root}/home/linuxbrew/.linuxbrew/sbin",
            )


if __name__ == "__main__":
    unittest.main()

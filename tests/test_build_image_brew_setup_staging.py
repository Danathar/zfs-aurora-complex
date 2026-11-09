"""
Script: tests/test_build_image_brew_setup_staging.py
What: Tests that the brew payload's first-boot unit stages its tarball in a private /tmp, and that the build notices if the base image stops staging where that containment reaches.
Doing: Asserts the text of the brew-setup.service drop-in, asserts the build installs it, and extracts the build-time staging check and runs it against fixture unit trees.
Why: `brew-setup.service` comes with the aurora-dx base image and stages its tarball through the fixed path /tmp/homebrew as root, so on a booted system any account that creates that name first chooses part of what lands in the prefix the unit then gives to UID 1000.
Goal: Keep the drop-in, its installation, and the check that backstops it from drifting apart.

The unit itself is not in this tree at any revision, so nothing here can assert what it
does. What the suite can pin is the three things this repository does about it: the
drop-in's content, the install line that puts it on the image, and the check that fails
the build if a future base image stages somewhere `PrivateTmp=` does not cover.
The check is extracted and executed rather than pattern-matched, because the property
under test is what it does to a unit file.
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

DROP_IN_RELATIVE = "usr/lib/systemd/system/brew-setup.service.d/10-private-tmp.conf"
DROP_IN = REPO_ROOT / "files" / DROP_IN_RELATIVE

CHECK_FUNCTION = "check_brew_setup_staging"

# Matches the check function from its opening line to the closing brace in column 1.
# build-image.sh defines no nested functions, so this is unambiguous.
CHECK_RE = re.compile(
    rf"^{CHECK_FUNCTION}\(\) \{{\n.*?^\}}\n",
    re.MULTILINE | re.DOTALL,
)

# The staging chain as the payload in the aurora-dx base writes it, read out of the
# published image (#284) rather than guessed from upstream's default branch.
PAYLOAD_UNIT = """[Unit]
Description=Setup Brew
ConditionPathExists=!/etc/.linuxbrew
ConditionPathExists=!/home/linuxbrew/.linuxbrew
ConditionPathExists=/usr/share/homebrew.tar.zst

[Service]
Type=oneshot
ExecStart=/usr/bin/mkdir -p /tmp/homebrew
ExecStart=/usr/bin/mkdir -p /home/linuxbrew
ExecStart=/usr/bin/tar --zstd -xf /usr/share/homebrew.tar.zst -C /tmp/homebrew
ExecStart=/usr/bin/cp -R -n /tmp/homebrew/home/linuxbrew/.linuxbrew /home/linuxbrew
ExecStart=/usr/bin/chown -R 1000:1000 /home/linuxbrew
ExecStart=/usr/bin/rm -rf /tmp/homebrew
ExecStart=/usr/bin/touch /etc/.linuxbrew
"""

UNIT_RELATIVE = "usr/lib/systemd/system/brew-setup.service"


def build_image_text() -> str:
    return BUILD_IMAGE.read_text(encoding="utf-8")


def drop_in_text() -> str:
    return DROP_IN.read_text(encoding="utf-8")


def check_source() -> str:
    """Return the staging check exactly as build-image.sh defines it."""

    match = CHECK_RE.search(build_image_text())
    assert match is not None, f"{CHECK_FUNCTION} not found in {BUILD_IMAGE}"
    return match.group(0)


def run_check(root: Path) -> subprocess.CompletedProcess[str]:
    """Run the real check body against a fixture tree rooted at `root`."""

    script = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"{check_source()}\n"
        f'{CHECK_FUNCTION} "$1"\n'
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


def fixture_unit(root: Path, content: str | None) -> None:
    """Place `content` where the base image puts brew-setup.service, or nothing at all."""

    if content is None:
        return
    path = root / UNIT_RELATIVE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class PremiseTests(unittest.TestCase):
    """What the rest of this file rests on."""

    def test_the_brew_payload_still_arrives_inside_the_aurora_base(self) -> None:
        # The unit comes with the aurora-dx base image, not from a COPY this
        # repository controls. A base without brew would mean the unit no longer
        # arrives from somewhere unreviewable, and this suite should be revisited
        # rather than left asserting something that protects nothing.
        self.assertIn(
            'ARG BASE_IMAGE="ghcr.io/ublue-os/aurora-dx:stable"',
            CONTAINERFILE.read_text(encoding="utf-8"),
        )

    def test_the_build_still_presets_the_unit_being_hardened(self) -> None:
        # The drop-in only matters because this image enables the unit at first boot.
        self.assertIn("systemctl preset brew-setup.service", build_image_text())


class DropInTests(unittest.TestCase):
    def test_the_drop_in_sets_private_tmp(self) -> None:
        text = drop_in_text()
        self.assertIn("[Service]", text)
        self.assertRegex(text, r"(?m)^PrivateTmp=yes$")

    def test_the_drop_in_sets_nothing_else(self) -> None:
        # Exactly one directive: anything more is a decision about a unit this
        # repository does not own, and belongs in review rather than in a quiet edit.
        directives = [
            line
            for line in drop_in_text().splitlines()
            if line and not line.startswith("#") and not line.startswith("[")
        ]
        self.assertEqual(directives, ["PrivateTmp=yes"])

    def test_the_drop_in_does_not_restate_the_payloads_commands(self) -> None:
        # Copying upstream's ExecStart= chain here is the failure mode the drop-in
        # exists to avoid: a copy drifts silently when the base image moves.
        code = "\n".join(
            line for line in drop_in_text().splitlines() if not line.lstrip().startswith("#")
        )
        self.assertNotIn("ExecStart=", code)

    def test_the_build_installs_the_drop_in_read_only(self) -> None:
        self.assertRegex(
            build_image_text(),
            r"install -D -m 0644 \\\n"
            rf"  /ctx/files/{re.escape(DROP_IN_RELATIVE)} \\\n"
            rf"  /{re.escape(DROP_IN_RELATIVE)}\n",
        )

    def test_the_installed_source_is_a_file_this_repository_tracks(self) -> None:
        self.assertTrue(DROP_IN.is_file(), f"{DROP_IN} is installed by the build but absent")


class StagingCheckTests(unittest.TestCase):
    def test_the_shipped_unit_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fixture_unit(root, PAYLOAD_UNIT)
            result = run_check(root)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_staging_under_var_tmp_passes(self) -> None:
        # PrivateTmp= covers /var/tmp as well, so a payload that moved there is still
        # contained and must not fail the build.
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fixture_unit(root, PAYLOAD_UNIT.replace("/tmp/homebrew", "/var/tmp/homebrew"))
            result = run_check(root)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_staging_directly_in_tmp_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fixture_unit(
                root,
                "[Service]\n"
                "Type=oneshot\n"
                "ExecStart=/usr/bin/tar --zstd -xf /usr/share/homebrew.tar.zst -C /tmp\n",
            )
            result = run_check(root)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_staging_moved_out_of_tmp_fails_the_build(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fixture_unit(root, PAYLOAD_UNIT.replace("/tmp/homebrew", "/run/homebrew"))
            result = run_check(root)
            self.assertEqual(result.returncode, 1)
            self.assertIn("no longer stages under /tmp or /var/tmp", result.stderr)
            # The offending commands are printed, so the reviewer does not have to go
            # extract the unit out of the base image to see what changed.
            self.assertIn("/run/homebrew", result.stderr)

    def test_a_tmp_path_outside_execstart_does_not_count(self) -> None:
        # A comment or a condition mentioning /tmp says nothing about where the unit
        # writes, so it must not satisfy the check.
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fixture_unit(
                root,
                "[Unit]\n"
                "ConditionPathExists=!/tmp/homebrew\n"
                "\n"
                "[Service]\n"
                "Type=oneshot\n"
                "# staging used to happen in /tmp/homebrew\n"
                "ExecStart=/usr/bin/tar --zstd -xf /usr/share/homebrew.tar.zst -C /run/homebrew\n",
            )
            result = run_check(root)
            self.assertEqual(result.returncode, 1)
            self.assertIn("no longer stages under /tmp or /var/tmp", result.stderr)

    def test_a_path_merely_ending_in_tmp_does_not_count(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fixture_unit(
                root,
                "[Service]\n"
                "ExecStart=/usr/bin/tar --zstd -xf /usr/share/homebrew.tar.zst "
                "-C /var/lib/brew-tmp\n",
            )
            result = run_check(root)
            self.assertEqual(result.returncode, 1)

    def test_a_missing_unit_fails_the_build(self) -> None:
        # The base image dropping brew-setup.service is not a quiet success: the drop-in
        # and the preset above would both be aimed at nothing.
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fixture_unit(root, None)
            result = run_check(root)
            self.assertEqual(result.returncode, 1)
            self.assertIn("missing from the base image", result.stderr)

    def test_the_check_actually_runs_in_the_build(self) -> None:
        # A defined-but-never-called check is the guard that guards nothing.
        code = "\n".join(
            line for line in build_image_text().splitlines() if not line.lstrip().startswith("#")
        )
        self.assertRegex(code, rf"(?m)^{CHECK_FUNCTION}$")

    def test_the_check_runs_after_it_is_defined(self) -> None:
        text = build_image_text()
        definition = text.index(f"{CHECK_FUNCTION}() {{")
        call = text.index(f"\n{CHECK_FUNCTION}\n")
        self.assertLess(definition, call)


if __name__ == "__main__":
    unittest.main()

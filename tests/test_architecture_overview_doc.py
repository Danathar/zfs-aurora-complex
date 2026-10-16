"""
Script: tests/test_architecture_overview_doc.py
What: Holds the image-build sections of docs/architecture-overview.md to the Containerfile, build_files/build-image.sh and the buildah action they describe, and runs build-image.sh's crun-krun pin.
Doing: Extracts the document's numbered step lists, joins each step to the line that performs it, and executes the two crun-krun lines of build-image.sh with `rpm` and `dnf5` replaced by recording stubs.
Why: build-image.sh runs only inside an image build, so nothing on the host read it: a reordered step, a stale doc claim, or an unpinned `crun-krun` install would all pass the suite and surface only as a drifted image on a booted machine.
Goal: Make a change to the image build that contradicts the overview, or that lets `crun-krun` upgrade the base image's `crun`, fail in CI.

`.coverage-thresholds.json` records build-image.sh as unmeasured: it does its
work at the top level, so importing or sourcing it would run the whole image
build. That stays true. What this file adds is narrower:

  * The step list under "`build-image.sh` then:" is a hand copy of the
    script's order. Each documented step is joined to the line that performs
    it, never to a mention in a comment, so a reorder in either file fails.
  * The `crun-krun` install (step 4 in the document) is the one decision the
    script makes itself rather than delegating to a measured Python helper.
    `crun-krun` Requires the exact `crun` version-release, so an unversioned
    install would upgrade the base image's `crun` whenever Fedora updates runs
    ahead of Aurora. The two lines that pin it are lifted out of the script
    and run under the script's own `set -euo pipefail`, with stubs for `rpm`
    and `dnf5` on PATH, so the test asserts what the build would actually ask
    dnf5 for -- and that a missing `crun` stops the build before dnf5 runs,
    which is the fail-closed claim the script's comment makes.
  * The Containerfile section's count word and its manifest-format claim. The
    latter named `oci: false`, an input of `redhat-actions/buildah-build`;
    this repository calls buildah itself with `--format docker`, so the name
    the doc told a reader to look for appeared nowhere in the tree.

Standard library only, for the reason tests/test_docs_consistency.py gives.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.test_docs_consistency import REPO_ROOT

DOC_PATH = REPO_ROOT / "docs" / "architecture-overview.md"
BUILD_IMAGE = "build_files/build-image.sh"


def read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def doc() -> str:
    return DOC_PATH.read_text(encoding="utf-8")


def numbered_list_after(needle: str, text: str | None = None) -> list[str]:
    """
    Return the numbered list items that follow `needle` in the document.

    The document writes its step lists as `1. text`, one per line, wrapped at
    the column limit with continuation lines indented. Collection stops at the
    first line that is neither a new item nor a continuation of one, so the
    paragraph after a list is never swallowed into it.
    """

    if text is None:
        text = doc()
    start = text.index(needle) + len(needle)
    items: list[str] = []
    for line in text[start:].splitlines():
        if not line.strip():
            if items:
                # A blank line inside a list ends it: the document separates
                # consecutive lists by one blank line and nothing else.
                break
            continue
        opener = re.match(r"^(\d+)\. (.*)$", line)
        if opener:
            if int(opener.group(1)) != len(items) + 1:
                raise AssertionError(f"list after {needle!r} is misnumbered at {line!r}")
            items.append(opener.group(2))
            continue
        if items and line.startswith(("   ", "\t")):
            items[-1] += " " + line.strip()
            continue
        break
    if not items:
        raise AssertionError(f"no numbered list follows {needle!r}")
    return items


class NumberedListParserTests(unittest.TestCase):
    """The parser the step-order tests stand on, checked against fixed text."""

    def parse(self, text: str) -> list[str]:
        return numbered_list_after("then:\n", text)

    def test_continuation_lines_join_their_item_and_the_next_paragraph_does_not(self) -> None:
        text = "then:\n\n1. one\n   wrapped\n2. two\nNot a step.\n"
        self.assertEqual(self.parse(text), ["one wrapped", "two"])

    def test_a_blank_line_ends_the_list(self) -> None:
        self.assertEqual(self.parse("then:\n1. one\n\n2. two\n"), ["one"])

    def test_a_misnumbered_list_is_refused(self) -> None:
        with self.assertRaisesRegex(AssertionError, "misnumbered"):
            self.parse("then:\n1. one\n3. three\n")

    def test_no_list_is_refused(self) -> None:
        with self.assertRaisesRegex(AssertionError, "no numbered list"):
            self.parse("then:\nprose\n")


class BuildImageStepOrderTests(unittest.TestCase):
    """The eight-step list for build_files/build-image.sh, in the script's order."""

    # One anchor per documented step, in the documented order. The anchors are
    # what the step acts on, not its wording: the doc may rephrase a step, but
    # not reorder one. Anchors are the lines that run, never a mention in a
    # comment: several of these names also appear in the script's prose, and
    # an anchor that matched the explanation instead of the step would report
    # an order the script does not have.
    ANCHORS = (
        ("installs the committed `cosign.pub`", 'install -m 0644 /ctx/cosign.pub "/etc/pki'),
        ("enables brew setup/update services", "\n/usr/bin/systemctl preset brew-setup.service\n"),
        ("keeps Distrobox", "\n# Distrobox is already included by upstream Aurora."),
        ("installs `crun-krun` with `dnf5`", '\ndnf5 -y install "crun-krun-${crun_evr}"\n'),
        (
            "runs the ZFS install helper",
            "\npython3 /ctx/containerfiles/zfs-akmods/install_zfs_from_akmods_cache.py\n",
        ),
        (
            "writes repository-specific signing policy",
            "\npython3 /ctx/files/scripts/configure_signing_policy.py\n",
        ),
        (
            "installs the local `tmpfiles.d` declaration",
            "\n  /usr/lib/tmpfiles.d/zfs-aurora-complex.conf\n",
        ),
        ("removes build-only runtime/container state", "\nrm -rf /var/lib/containers\n"),
    )

    def setUp(self) -> None:
        self.items = numbered_list_after("`build-image.sh` then:\n")
        self.script = read(BUILD_IMAGE)

    def test_the_documented_list_is_the_length_this_test_covers(self) -> None:
        self.assertEqual(len(self.items), len(self.ANCHORS))

    def test_every_documented_step_says_what_this_test_thinks_it_says(self) -> None:
        for item, (phrase, _) in zip(self.items, self.ANCHORS):
            with self.subTest(step=phrase):
                self.assertIn(phrase, item)

    def test_the_script_performs_the_documented_steps_in_the_documented_order(self) -> None:
        positions = []
        for phrase, anchor in self.ANCHORS:
            with self.subTest(step=phrase):
                self.assertIn(anchor, self.script)
                positions.append(self.script.index(anchor))
        self.assertEqual(positions, sorted(positions), "build-image.sh reordered its steps")

    def test_the_crun_krun_step_states_the_pin(self) -> None:
        step = self.items[3]
        self.assertIn("pinned to the exact `crun` version-release", step)
        self.assertIn("the build fails instead", step)

    def test_the_signing_policy_step_names_the_repository_the_containerfile_passes(self) -> None:
        self.assertIn("for `ghcr.io/danathar/zfs-aurora-complex`", self.items[5])
        self.assertIn(
            'ARG IMAGE_REPO="ghcr.io/danathar/zfs-aurora-complex"\n', read("Containerfile")
        )


class CrunKrunPinTests(unittest.TestCase):
    """Run build-image.sh's own crun-krun lines with rpm and dnf5 stubbed."""

    QUERY_FORMAT = "%{EPOCHNUM}:%{VERSION}-%{RELEASE}"
    EVR = "0:1.24-1.fc43"

    @staticmethod
    def pin_block(script: str) -> str:
        """
        The executable lines from the `crun_evr=` assignment through the dnf5
        install that consumes it. Comment lines are dropped so the block is
        exactly what bash runs; anything else inserted between the two lines
        -- an `|| true`, a fallback install -- is kept and executed.
        """

        lines = script.splitlines()
        start = next(i for i, line in enumerate(lines) if line.startswith("crun_evr="))
        end = next(i for i in range(start, len(lines)) if "crun-krun-" in lines[i])
        return "\n".join(
            line for line in lines[start : end + 1] if not line.lstrip().startswith("#")
        )

    def run_block(
        self, *, rpm_exit: int = 0, dnf_exit: int = 0
    ) -> tuple[subprocess.CompletedProcess, list, list]:
        script = read(BUILD_IMAGE)
        # The fail-closed claim rests on the script's own `set -e`, so take the
        # strict-mode line from the script rather than supplying one here.
        strict = "\nset -euo pipefail\n"
        self.assertIn(strict, script)
        self.assertLess(script.index(strict), script.index("\ncrun_evr="))
        block = self.pin_block(script)

        with tempfile.TemporaryDirectory() as tmp:
            bindir = Path(tmp) / "bin"
            bindir.mkdir()
            log = Path(tmp) / "calls"
            stubs = {
                "rpm": (
                    f'for a in "$@"; do printf "rpm\\t%s\\n" "$a" >>"{log}"; done\n'
                    f'printf "rpm\\t--end\\n" >>"{log}"\n'
                    + (
                        f'printf "{self.EVR}"\n'
                        if rpm_exit == 0
                        else "echo 'package crun is not installed'\n"
                    )
                    + f"exit {rpm_exit}\n"
                ),
                "dnf5": (
                    f'for a in "$@"; do printf "dnf5\\t%s\\n" "$a" >>"{log}"; done\n'
                    f'printf "dnf5\\t--end\\n" >>"{log}"\n'
                    f"exit {dnf_exit}\n"
                ),
            }
            for name, body in stubs.items():
                path = bindir / name
                path.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
                path.chmod(path.stat().st_mode | stat.S_IXUSR)

            program = f"{strict.strip()}\n{block}\necho REACHED_END\n"
            env = {**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}"}
            result = subprocess.run(
                ["bash", "-c", program],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
            calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []

        def argv_of(tool: str) -> list[list[str]]:
            runs: list[list[str]] = []
            current: list[str] = []
            for entry in calls:
                who, _, arg = entry.partition("\t")
                if who != tool:
                    continue
                if arg == "--end":
                    runs.append(current)
                    current = []
                else:
                    current.append(arg)
            return runs

        return result, argv_of("rpm"), argv_of("dnf5")

    def test_dnf5_is_asked_for_crun_krun_at_the_installed_crun_evr(self) -> None:
        result, rpm_runs, dnf_runs = self.run_block()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("REACHED_END", result.stdout)
        self.assertEqual(rpm_runs, [["-q", "--qf", self.QUERY_FORMAT, "crun"]])
        self.assertEqual(dnf_runs, [["-y", "install", f"crun-krun-{self.EVR}"]])

    def test_a_missing_crun_stops_the_build_before_dnf5_runs(self) -> None:
        result, rpm_runs, dnf_runs = self.run_block(rpm_exit=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("REACHED_END", result.stdout)
        self.assertEqual(len(rpm_runs), 1)
        self.assertEqual(dnf_runs, [])

    def test_an_unavailable_pinned_build_stops_the_build(self) -> None:
        result, _, dnf_runs = self.run_block(dnf_exit=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("REACHED_END", result.stdout)
        self.assertEqual(len(dnf_runs), 1)

    def test_crun_krun_is_installed_nowhere_else_in_the_image_build(self) -> None:
        # A second, unversioned install anywhere else would undo the pin.
        for path in (BUILD_IMAGE, "Containerfile"):
            with self.subTest(path=path):
                executable = [
                    line
                    for line in read(path).splitlines()
                    if "crun-krun" in line and not line.lstrip().startswith("#")
                ]
                expected = (
                    ['dnf5 -y install "crun-krun-${crun_evr}"'] if path == BUILD_IMAGE else []
                )
                self.assertEqual(executable, expected)


class ContainerfileSectionTests(unittest.TestCase):
    """Section "3. Native Final Image Build" enumerates the Containerfile."""

    OPENER = re.compile(r"It does (\w+) important things:")

    def test_the_containerfile_list_count_word_matches_its_length(self) -> None:
        opener = self.OPENER.search(doc())
        self.assertIsNotNone(opener, "the Containerfile section stopped opening its list")
        words = {"three": 3, "four": 4, "five": 5, "six": 6}
        self.assertIn(opener.group(1), words, "unhandled count word")
        self.assertEqual(words[opener.group(1)], len(numbered_list_after(opener.group(0))))

    def test_each_documented_containerfile_step_names_something_in_it(self) -> None:
        containerfile = read("Containerfile")
        needles = {
            1: 'ARG BASE_IMAGE="ghcr.io/ublue-os/aurora-dx:',
            2: "\n# FROM ${BREW_IMAGE} AS brew\n",
            3: "\n    /ctx/build-image.sh\n",
            4: "\nRUN bootc container lint\n",
        }
        items = numbered_list_after(self.OPENER.search(doc()).group(0))
        self.assertEqual(len(items), len(needles))
        for number, needle in needles.items():
            with self.subTest(step=number):
                self.assertIn(needle, containerfile)
        positions = [containerfile.index(needle) for needle in needles.values()]
        self.assertEqual(positions, sorted(positions))

    def test_the_brew_stage_is_left_commented_as_documented(self) -> None:
        self.assertIn("optional `ublue-os/brew` payload stage left commented", doc())
        containerfile = read("Containerfile")
        self.assertNotRegex(containerfile, r"(?m)^FROM \$\{BREW_IMAGE\}")
        self.assertNotRegex(containerfile, r"(?m)^COPY --from=brew ")

    def test_the_documented_manifest_format_flag_is_the_one_buildah_gets(self) -> None:
        stated = re.search(r"uses Docker v2s2 manifest format \(`([^`]+)`\)", doc())
        self.assertIsNotNone(stated, "the overview stopped naming a manifest-format flag")
        action = read(".github/actions/build-native-image/action.yml")
        self.assertIn(stated.group(1), action)
        self.assertIn("          --format docker \\\n", action)


if __name__ == "__main__":
    unittest.main()

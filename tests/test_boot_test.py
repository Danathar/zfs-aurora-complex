"""
Script: tests/test_boot_test.py
What: Tests for the nightly boot test's guest script, its result parsing, its verdict, its retry rule,
    and the bcvk command line and injected units that get the guest to run the checks at all.
Doing: Runs the generated guest script under bash with stub `rpm`, `zpool`, `zfs`, `systemctl` and
    `timeout` commands, feeds its output and hand-written SSH sessions through the parser and judge,
    reads the units and bcvk command boot() builds, runs boot() with subprocess replaced, and drives
    main() with the registry, pull and VM calls replaced.
Why: The verdict is read back from marked lines of an SSH session, so a quoting slip in the script, a
    marker the parser misses, or a judge that passes a check it never saw would turn the only boot of
    the published image into a green run that checked nothing.
Goal: Keep "green" meaning the image booted, was logged into, and had ZFS installed and loaded.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ci_tools.boot_test as script
from ci_tools.boot_test import (
    BOOT_TIMEOUT_SECONDS,
    GUEST_CHECKS,
    GUEST_SCRIPT_MOUNT,
    MARKER,
    ROOTHOME_UNIT,
    GuestReport,
    boot_command,
    guest_script,
    judge,
    parse_guest_output,
    pinned_reference,
    write_units,
)
from ci_tools.common import CiToolError

ALL_PASS = [{"name": name, "status": "pass", "detail": ""} for name, _ in GUEST_CHECKS]


def passing_session() -> str:
    lines = [f"{MARKER}STATE degraded", f"{MARKER}INFO kernel 7.2.11-200.fc44.x86_64"]
    lines += [f"{MARKER}CHECK {name} pass detail" for name, _ in GUEST_CHECKS]
    lines += [f"{MARKER}FAILED-UNIT bootloader-update.service", f"{MARKER}DONE"]
    return "\n".join(lines) + "\n"


STUB = """#!/bin/bash
echo "$(basename "$0") SYSTEMD_PAGER=${SYSTEMD_PAGER-unset} $*" >> "$STUB_LOG"
case "$(basename "$0") $1" in
  "rpm -q") [ "$2" = zfs ] && echo "zfs-2.4.4-1.fc44.x86_64" && exit 0; exit 1 ;;
  "zpool list") echo "no pools available"; exit 0 ;;
  "zfs version") echo "zfs-2.4.4-1"; exit 0 ;;
  "systemctl is-system-running") echo running; exit 0 ;;
  "systemctl --failed") echo "mcelog.service loaded failed failed Machine Check"; exit 0 ;;
esac
exit 0
"""

# Logs that it ran, then runs the rest of its arguments. Its log line is how a
# test sees which commands the guest script bounds with `timeout`.
TIMEOUT_STUB = """#!/bin/bash
echo "timeout $*" >> "$STUB_LOG"
shift
exec "$@"
"""

# A systemd that never reports a state, as when `--wait` is cut off.
SILENT_SYSTEMCTL_STUB = """#!/bin/bash
echo "systemctl $*" >> "$STUB_LOG"
exit 1
"""


@unittest.skipIf(shutil.which("bash") is None, "bash is not installed")
class GuestScriptTests(unittest.TestCase):
    """The generated script, run for real, against stub commands."""

    def run_script(self, overrides: dict[str, str] | None = None) -> tuple[GuestReport, str]:
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            stubs = {"rpm": STUB, "zpool": STUB, "zfs": STUB, "systemctl": STUB, **(overrides or {})}
            for name, body in stubs.items():
                stub = bin_dir / name
                stub.write_text(body, encoding="utf-8")
                stub.chmod(0o755)
            log = Path(tmp) / "calls.log"
            env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "STUB_LOG": str(log)}
            proc = subprocess.run(
                ["bash", "-c", guest_script()],
                capture_output=True,
                text=True,
                env=env,
                check=False,
                timeout=60,
            )
            return parse_guest_output(proc.stdout), log.read_text(encoding="utf-8")

    def test_every_check_reports_and_the_script_finishes(self) -> None:
        report, _ = self.run_script()
        self.assertTrue(report.done)
        self.assertEqual(report.state, "running")
        self.assertEqual([c["name"] for c in report.checks], [name for name, _ in GUEST_CHECKS])
        self.assertEqual(report.failed_units, ["mcelog.service"])
        self.assertEqual(report.zfs_version, ["zfs-2.4.4-1"])

    def test_a_check_reports_its_own_outcome_and_first_line(self) -> None:
        # The stub has zfs but not kmod-zfs; /sys/module/zfs depends on the host.
        report, _ = self.run_script()
        checks = {c["name"]: c for c in report.checks}
        self.assertEqual(checks["package:zfs"], {"name": "package:zfs", "status": "pass", "detail": "zfs-2.4.4-1.fc44.x86_64"})
        self.assertEqual(checks["package:kmod-zfs"]["status"], "fail")
        self.assertEqual(checks["zpool:list"]["detail"], "no pools available")
        expected_module = "pass" if Path("/sys/module/zfs").is_dir() else "fail"
        self.assertEqual(checks["module:zfs"]["status"], expected_module)

    def test_systemctl_never_gets_a_pager(self) -> None:
        # bcvk gives the session a terminal; a paged `systemctl --failed` waited
        # for a key until the boot timed out.
        _, calls = self.run_script()
        systemctl = [line for line in calls.splitlines() if line.startswith("systemctl ")]
        self.assertTrue(systemctl)
        for line in systemctl:
            self.assertIn("SYSTEMD_PAGER= ", line)

    def test_the_settle_wait_and_zpool_are_bounded_by_timeout(self) -> None:
        # A unit that never finishes starting, or a zpool stuck on /dev/zfs,
        # must not hold the session until BOOT_TIMEOUT_SECONDS kills it with
        # the remaining checks unrun.
        _, calls = self.run_script({"timeout": TIMEOUT_STUB})
        bounded = [line.split()[2:4] for line in calls.splitlines() if line.startswith("timeout ")]
        self.assertIn(["systemctl", "is-system-running"], bounded)
        self.assertIn(["zpool", "list"], bounded)

    def test_a_state_systemd_never_reported_reads_as_timeout(self) -> None:
        report, _ = self.run_script({"systemctl": SILENT_SYSTEMCTL_STUB})
        self.assertEqual(report.state, "timeout")
        self.assertTrue(report.done)


class ModuleCheckOrderTests(unittest.TestCase):
    """module:zfs has to see the module as the boot left it."""

    def test_nothing_runs_zfs_or_zpool_before_the_module_check(self) -> None:
        # OpenZFS's libzfs_init runs `modprobe zfs` when the module is absent, so
        # a zfs or zpool command ahead of module:zfs would load the module and
        # pass a boot whose modules-load.d entry never loaded it.
        lines = guest_script().splitlines()
        module_check = next(i for i, line in enumerate(lines) if "/sys/module/zfs" in line)
        callers = [i for i, line in enumerate(lines) if re.search(r"(?<![\w/:-])(zfs|zpool) ", line)]
        self.assertTrue(callers, "the script no longer runs zfs or zpool at all")
        self.assertGreater(min(callers), module_check, [lines[i] for i in callers])


class BootTimeoutTests(unittest.TestCase):
    """A boot that hits the timeout after the guest printed its results."""

    def test_the_checks_printed_before_the_timeout_are_kept(self) -> None:
        # subprocess.TimeoutExpired carries bytes even with text=True. Read as a
        # bytes repr, a failed check would look like checks that never ran, and
        # the retry could turn the run green.
        failing = passing_session().replace("CHECK module:zfs pass", "CHECK module:zfs fail")

        def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            if command[0] == "bcvk":
                raise subprocess.TimeoutExpired(command, 1, output=failing.encode(), stderr=b"")
            return subprocess.CompletedProcess(command, 0)

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(script.subprocess, "run", side_effect=fake_run),
            contextlib.redirect_stdout(open(os.devnull, "w")),
        ):
            report, output = script.boot("ghcr.io/danathar/zfs-aurora-complex@sha256:" + "c" * 64, Path(tmp), 1)
        self.assertTrue(report.done)
        statuses = {check["name"]: check["status"] for check in report.checks}
        self.assertEqual(statuses["module:zfs"], "fail")
        self.assertNotIn("b'", output)
        self.assertIn("bcvk timed out", output)


class WriteUnitsTests(unittest.TestCase):
    """The units bcvk injects: without them the VM boots but SSH never answers."""

    def test_sshd_and_the_roothome_unit_are_wanted_by_default_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            units = write_units(Path(tmp) / "units")
            system = units / "system"
            wants = system / "default.target.wants"
            self.assertEqual((system / "zac-roothome.service").read_text(encoding="utf-8"), ROOTHOME_UNIT)
            self.assertEqual(os.readlink(wants / "sshd.service"), "/usr/lib/systemd/system/sshd.service")
            self.assertEqual((wants / "zac-roothome.service").resolve(), (system / "zac-roothome.service").resolve())

    def test_writing_the_units_again_keeps_the_links(self) -> None:
        # boot() writes them on every attempt into the same directory.
        with tempfile.TemporaryDirectory() as tmp:
            write_units(Path(tmp) / "units")
            wants = write_units(Path(tmp) / "units") / "system" / "default.target.wants"
            self.assertEqual(sorted(link.name for link in wants.iterdir()), ["sshd.service", "zac-roothome.service"])

    def test_the_roothome_unit_runs_before_tmpfiles_and_only_when_missing(self) -> None:
        # tmpfiles writes root's SSH key through /root -> /var/roothome, so the
        # directory has to exist first; an installed system already has it.
        lines = ROOTHOME_UNIT.splitlines()
        self.assertIn("Before=systemd-tmpfiles-setup.service", lines)
        self.assertIn("ConditionPathExists=!/var/roothome", lines)
        self.assertIn("ExecStart=/usr/bin/mkdir -p -m 0700 /var/roothome", lines)


class BootCommandTests(unittest.TestCase):
    """The bcvk command line: where the guest finds its script and its units."""

    def command(self) -> list[str]:
        return boot_command(
            "registry.example/image@sha256:" + "c" * 64,
            units=Path("units"),
            script_dir=Path("checks"),
            log_dir=Path("boot-1"),
            name="zac-boot-test-1-1",
        )

    def option(self, command: list[str], flag: str) -> str:
        return command[command.index(flag) + 1]

    def test_every_host_path_is_absolute(self) -> None:
        # A relative --systemd-units path was silently ignored in a local run,
        # and the VM booted without sshd.
        with tempfile.TemporaryDirectory() as tmp, contextlib.chdir(tmp):
            command = self.command()
            cwd = Path.cwd()
        self.assertEqual(self.option(command, "--systemd-units"), str(cwd / "units"))
        self.assertEqual(self.option(command, "--ro-bind"), f"{cwd / 'checks'}:{GUEST_SCRIPT_MOUNT}")
        self.assertEqual(self.option(command, "--log-dir"), f"journal,console={cwd / 'boot-1'}/")

    def test_the_guest_runs_the_script_from_where_the_bind_mount_puts_it(self) -> None:
        command = self.command()
        mount = self.option(command, "--ro-bind").rsplit(":", 1)[1]
        separator = command.index("--")
        self.assertEqual(command[separator - 1], "registry.example/image@sha256:" + "c" * 64)
        self.assertEqual(command[separator + 1:], [f"bash /run/virtiofs-mnt-{mount}/checks.sh"])


class BootTests(unittest.TestCase):
    """boot(): one VM run, what it leaves on disk, and the cleanup after it."""

    IMAGE = "ghcr.io/danathar/zfs-aurora-complex@sha256:" + "c" * 64

    def run_boot(self, work: Path, attempt: int, bcvk: object) -> tuple[GuestReport, str, list[tuple[list[str], dict]]]:
        calls: list[tuple[list[str], dict]] = []

        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append((command, kwargs))
            if command[0] == "bcvk":
                if isinstance(bcvk, BaseException):
                    raise bcvk
                return subprocess.CompletedProcess(command, 0, stdout=bcvk, stderr="bcvk: warning on stderr\n")
            return subprocess.CompletedProcess(command, 0)

        with (
            patch.object(script.subprocess, "run", side_effect=fake_run),
            contextlib.redirect_stdout(open(os.devnull, "w")),
        ):
            report, output = script.boot(self.IMAGE, work, attempt)
        return report, output, calls

    def test_the_guest_gets_the_generated_script_at_the_mounted_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, _, calls = self.run_boot(Path(tmp), 1, passing_session())
            command = calls[0][0]
            host, mount = command[command.index("--ro-bind") + 1].rsplit(":", 1)
            guest_path = command[-1].split()[-1]
            self.assertEqual(guest_path, f"/run/virtiofs-mnt-{mount}/checks.sh")
            self.assertEqual((Path(host) / "checks.sh").read_text(encoding="utf-8"), guest_script())
            self.assertTrue((Path(tmp) / "units" / "system" / "default.target.wants" / "sshd.service").is_symlink())

    def test_bcvk_is_bounded_and_its_vm_removed_afterwards(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report, _, calls = self.run_boot(Path(tmp), 1, passing_session())
        self.assertTrue(report.done)
        (bcvk, bcvk_kwargs), (cleanup, _) = calls
        self.assertEqual(bcvk_kwargs.get("timeout"), BOOT_TIMEOUT_SECONDS)
        name = bcvk[bcvk.index("--name") + 1]
        self.assertEqual(cleanup, ["podman", "rm", "--force", "--ignore", name])

    def test_a_timed_out_vm_is_removed_too(self) -> None:
        # Killing bcvk leaves the VM's container running into the next attempt.
        with tempfile.TemporaryDirectory() as tmp:
            timeout = subprocess.TimeoutExpired(["bcvk"], 1, output=b"", stderr=None)
            _, output, calls = self.run_boot(Path(tmp), 2, timeout)
        name = calls[0][0][calls[0][0].index("--name") + 1]
        self.assertEqual(calls[-1][0], ["podman", "rm", "--force", "--ignore", name])
        self.assertIn("bcvk timed out", output)

    def test_each_attempt_keeps_its_own_log_with_stdout_and_stderr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.run_boot(Path(tmp), 1, "first boot\n")
            _, output, _ = self.run_boot(Path(tmp), 2, "second boot\n")
            first = (Path(tmp) / "boot-1" / "bcvk.log").read_text(encoding="utf-8")
            second = (Path(tmp) / "boot-2" / "bcvk.log").read_text(encoding="utf-8")
        self.assertIn("first boot", first)
        self.assertIn("second boot", second)
        self.assertEqual(second, output)
        self.assertIn("bcvk: warning on stderr", second)


class ParseTests(unittest.TestCase):
    def test_markers_are_found_after_terminal_noise(self) -> None:
        output = f"\x1b]11;?\x1b\\\x1b[6n{MARKER}STATE running\nmotd line\n{MARKER}DONE\n"
        report = parse_guest_output(output)
        self.assertEqual(report.state, "running")
        self.assertTrue(report.done)

    def test_a_detail_keeps_its_spaces(self) -> None:
        report = parse_guest_output(f"{MARKER}CHECK zpool:list pass no pools available\n")
        self.assertEqual(report.checks, [{"name": "zpool:list", "status": "pass", "detail": "no pools available"}])

    def test_empty_failed_unit_and_zfs_version_lines_are_dropped(self) -> None:
        # The guest's `while read` loops print a marker for a blank line too.
        report = parse_guest_output(f"{MARKER}FAILED-UNIT \n{MARKER}INFO zfs-version \n{MARKER}INFO uptime 5s\n")
        self.assertEqual(report.failed_units, [])
        self.assertEqual(report.zfs_version, [])
        self.assertEqual(report.kernel, "")


class JudgeTests(unittest.TestCase):
    def test_every_check_passing_passes_whatever_units_failed(self) -> None:
        report = GuestReport(done=True, state="degraded", checks=ALL_PASS, failed_units=["mcelog.service"])
        self.assertTrue(judge(report)[0])

    def test_checks_that_never_finished_fail(self) -> None:
        passed, summary = judge(GuestReport(done=False, checks=ALL_PASS))
        self.assertFalse(passed)
        self.assertIn("never reached", summary)

    def test_a_failed_check_fails_and_is_named(self) -> None:
        checks = [dict(c) for c in ALL_PASS]
        checks[2]["status"] = "fail"
        passed, summary = judge(GuestReport(done=True, checks=checks))
        self.assertFalse(passed)
        self.assertIn(GUEST_CHECKS[2][0], summary)

    def test_a_check_with_no_result_fails(self) -> None:
        passed, summary = judge(GuestReport(done=True, checks=ALL_PASS[:-1]))
        self.assertFalse(passed)
        self.assertIn(GUEST_CHECKS[-1][0], summary)


class PinnedReferenceTests(unittest.TestCase):
    def test_cases(self) -> None:
        digest = "sha256:" + "a" * 64
        cases = {
            "ghcr.io/danathar/zfs-aurora-complex:latest": "ghcr.io/danathar/zfs-aurora-complex",
            "ghcr.io/danathar/zfs-aurora-complex": "ghcr.io/danathar/zfs-aurora-complex",
            "localhost:5000/aurora:candidate": "localhost:5000/aurora",
            "localhost:5000/aurora": "localhost:5000/aurora",
            f"ghcr.io/danathar/zfs-aurora-complex@sha256:{'b' * 64}": "ghcr.io/danathar/zfs-aurora-complex",
        }
        for image, repository in cases.items():
            with self.subTest(image=image):
                self.assertEqual(pinned_reference(image, digest), f"{repository}@{digest}")


class MainTests(unittest.TestCase):
    """main(): which digest is booted, when a boot is retried, and the exit."""

    DIGEST = "sha256:" + "c" * 64

    def run_main(self, sessions: list[str]) -> tuple[list[str], dict, str, Exception | None]:
        booted: list[str] = []
        self.pulls: list[list[str]] = []

        def fake_boot(image: str, work: Path, attempt: int) -> tuple[GuestReport, str]:
            booted.append(image)
            output = sessions[attempt - 1]
            return parse_guest_output(output), output

        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            summary_path = Path(tmp) / "summary.md"
            summary_path.write_text("earlier step\n", encoding="utf-8")
            env = {
                "BOOT_TEST_IMAGE": "ghcr.io/danathar/zfs-aurora-complex:latest",
                "BOOT_TEST_WORK": tmp,
                "GITHUB_STEP_SUMMARY": str(summary_path),
            }
            stack.enter_context(patch.dict(os.environ, env))
            stack.enter_context(patch.object(script, "skopeo_inspect_digest", return_value=self.DIGEST))
            stack.enter_context(
                patch.object(script, "run_cmd", side_effect=lambda cmd, **_: self.pulls.append(cmd) or "")
            )
            stack.enter_context(patch.object(script, "boot", side_effect=fake_boot))
            stack.enter_context(contextlib.redirect_stdout(open(os.devnull, "w")))  # noqa: SIM115
            error = None
            try:
                script.main()
            except CiToolError as exc:
                error = exc
            result = json.loads((Path(tmp) / "result.json").read_text(encoding="utf-8"))
            return booted, result, summary_path.read_text(encoding="utf-8"), error

    def test_a_passing_boot_boots_the_digest_once_and_succeeds(self) -> None:
        booted, result, summary, error = self.run_main([passing_session()])
        self.assertIsNone(error)
        self.assertEqual(booted, [f"ghcr.io/danathar/zfs-aurora-complex@{self.DIGEST}"])
        self.assertTrue(result["passed"])
        self.assertIn(self.DIGEST, summary)

    def test_the_digest_is_pulled_and_the_tag_is_recorded_beside_it(self) -> None:
        _, result, _, _ = self.run_main([passing_session()])
        pinned = f"ghcr.io/danathar/zfs-aurora-complex@{self.DIGEST}"
        self.assertEqual([(cmd[:2], cmd[-1]) for cmd in self.pulls], [(["podman", "pull"], pinned)])
        self.assertEqual(result["image"], "ghcr.io/danathar/zfs-aurora-complex:latest")
        self.assertEqual(result["booted"], pinned)

    def test_the_summary_is_appended_and_shows_what_the_guest_reported(self) -> None:
        session = passing_session().replace(f"{MARKER}DONE", f"{MARKER}INFO zfs-version zfs-2.4.4-1\n{MARKER}DONE")
        _, _, summary, _ = self.run_main([session])
        self.assertTrue(summary.startswith("earlier step\n## Boot test\n"))
        self.assertIn("| `zfs version` | `zfs-2.4.4-1` |", summary)
        self.assertIn("Failed units (not judged): `bootloader-update.service`", summary)
        self.assertNotIn("bcvk output (tail)", summary)

    def test_a_boot_that_never_ran_the_checks_is_retried_once(self) -> None:
        booted, result, _, error = self.run_main(["Timeout waiting for readiness", passing_session()])
        self.assertIsNone(error)
        self.assertEqual(len(booted), 2)
        self.assertEqual(result["attempts"], 2)

    def test_a_failed_check_is_not_retried_and_fails_the_run(self) -> None:
        failing = passing_session().replace("CHECK module:zfs pass", "CHECK module:zfs fail")
        booted, result, summary, error = self.run_main([failing, passing_session()])
        self.assertEqual(len(booted), 1)
        self.assertFalse(result["passed"])
        self.assertIsInstance(error, CiToolError)
        self.assertIn("module:zfs", summary)
        self.assertIn("bcvk output (tail)", summary)
        self.assertIn(f"{MARKER}DONE", summary.split("bcvk output (tail)", 1)[1])

    def test_two_boots_that_never_ran_the_checks_fail_the_run(self) -> None:
        booted, result, _, error = self.run_main(["no ssh", "no ssh"])
        self.assertEqual(len(booted), 2)
        self.assertFalse(result["passed"])
        self.assertIsInstance(error, CiToolError)


if __name__ == "__main__":
    unittest.main()

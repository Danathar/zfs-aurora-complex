"""
Script: ci_tools/boot_test.py
What: Boots one published image in a throwaway VM and checks, over SSH, that ZFS is installed and its kernel module loaded.
Doing: Resolves the image tag to a digest, pulls that digest, boots it with `bcvk ephemeral run-ssh`, runs a
    check script in the guest, and reads the results back from marked lines of the SSH session's output.
Why: Every other check here inspects the image as files -- packages, module files, signatures. None of them
    boots it, so an image whose ZFS module does not load on its own kernel could sit at `:latest` unnoticed
    until a machine tracking it rebooted into it.
Goal: A red run when the image a user would pull does not boot, or boots without working ZFS.

It reports; it gates nothing. `.github/workflows/boot-test.yml` runs it after every build.yml run on
main, against whatever `:latest` is then. No pool is created on a real device: the VM has no disk but
the image's own root, and `zpool list` only proves userspace reaches the module.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ci_tools.common import (
    CiToolError,
    optional_env,
    require_env,
    run_cmd_with_retries,
    skopeo_inspect_digest,
)

# The guest writes one marker per line so the host can read results out of an
# SSH session that also carries whatever the image prints at login. The marker
# is looked for anywhere in a line, not only at its start, because a login
# shell can print terminal queries ahead of the first one.
MARKER = "ZAC-BOOT-"

# What the guest checks. Each is a name, then the bash that decides it: exit 0
# passes, and the first line it prints is kept as the detail.
#
# - package:zfs and package:kmod-zfs are the userspace and the kernel-module
#   packages install_zfs_from_akmods_cache.py installs.
# - module:zfs is the module loaded *at boot*, by files/usr/lib/modules-load.d/
#   zfs.conf. Nothing loads it by hand first, so a module that only loads on
#   request, or does not load against the running kernel at all, fails here.
# - zpool:list proves userspace reaches the loaded module through /dev/zfs.
#   The VM has no pool, so success is "no pools available".
#
# These prove the image boots with working ZFS, not whose build of ZFS it is.
# Stock ghcr.io/ublue-os/aurora-dx:stable passed all four in a hosted run
# (39126496706): the base ships kmod-zfs and loads it at boot as well.
GUEST_CHECKS: tuple[tuple[str, str], ...] = (
    ("package:zfs", "rpm -q zfs"),
    ("package:kmod-zfs", "rpm -q kmod-zfs"),
    ("module:zfs", "test -d /sys/module/zfs && cat /sys/module/zfs/version"),
    ("zpool:list", "timeout 60 zpool list 2>&1"),
)

# Two things bcvk needs from the guest that an installed Aurora has or does
# not need, injected into the boot (the image itself is not changed):
#
# - bcvk provisions root's SSH key through a systemd credential that tmpfiles
#   writes to /root/.ssh. On an ostree image /root is a symlink to
#   /var/roothome, which an installed system has from its deployment but an
#   ephemeral /var built from the image does not, so the key was never written.
#   This unit creates the directory before tmpfiles runs.
# - bcvk connects to TCP port 22 through QEMU's user network, so sshd.service
#   has to run. It is started through a default.target.wants link, whether or
#   not the image enables it.
#
# The same two units atomic-image-builder's and zfs-kinoite-complex's boot
# tests inject, for the same reasons.
ROOTHOME_UNIT = """\
[Unit]
Description=Create /var/roothome so systemd credentials can provision /root/.ssh
DefaultDependencies=no
After=local-fs.target
Before=systemd-tmpfiles-setup.service
ConditionPathExists=!/var/roothome

[Service]
Type=oneshot
ExecStart=/usr/bin/mkdir -p -m 0700 /var/roothome

[Install]
WantedBy=default.target
"""

VM_MEMORY = "4G"
VM_VCPUS = "2"
# Seconds the guest waits for boot to settle before it checks anyway. A unit
# that never finishes starting must not stop the ZFS checks from running.
SETTLE_TIMEOUT_SECONDS = 300
# Seconds for one whole boot, checks included. bcvk gives up on SSH by itself
# after four minutes; this bounds a VM that answers SSH and then hangs.
BOOT_TIMEOUT_SECONDS = 15 * 60
# A second boot absorbs nested-VM failures that have nothing to do with the
# image, such as a slow runner missing bcvk's SSH deadline. It is only taken
# when the guest reported no check at all. Once any check has reported, the
# VM booted and logged in, and that boot is the answer: a failed check that
# a later hang cut off before DONE must not be replaced by a second boot.
BOOT_ATTEMPTS = 2
# bcvk mounts a --ro-bind directory at /run/virtiofs-mnt-<name> in the guest.
GUEST_SCRIPT_MOUNT = "zac-checks"
LOG_TAIL_LINES = 40
PULL_TIMEOUT_SECONDS = 30 * 60


def guest_script() -> str:
    """The checks, as a bash script the guest runs once SSH is up."""
    lines = [
        "set -u",
        # bcvk gives the session a terminal, so systemctl would page its output
        # through less and wait for a key that never comes.
        "export SYSTEMD_PAGER= PAGER=cat",
        f'state="$(timeout {SETTLE_TIMEOUT_SECONDS} systemctl is-system-running --wait 2>/dev/null)"',
        f'echo "{MARKER}STATE ${{state:-timeout}}"',
        f'echo "{MARKER}INFO kernel $(uname -r)"',
    ]
    for name, command in GUEST_CHECKS:
        lines += [
            f"if out=\"$({command})\"; then status=pass; else status=fail; fi",
            f'echo "{MARKER}CHECK {name} $status $(printf %s "$out" | head -n 1)"',
        ]
    # Only after the checks: OpenZFS's libzfs_init runs `modprobe zfs` when the
    # module is absent, so any zfs or zpool command before module:zfs would load
    # the module and hide a boot that failed to. zpool:list comes after
    # module:zfs in GUEST_CHECKS for the same reason.
    lines.append(
        f'zfs version 2>/dev/null | while read -r line; do echo "{MARKER}INFO zfs-version $line"; done'
    )
    lines += [
        "systemctl --failed --plain --no-legend --full | while read -r unit _; do",
        f'  echo "{MARKER}FAILED-UNIT $unit"',
        "done",
        f'echo "{MARKER}DONE"',
    ]
    return "\n".join(lines) + "\n"


@dataclass
class GuestReport:
    """What the guest printed, read back from its marked lines."""

    done: bool = False
    state: str = ""
    kernel: str = ""
    zfs_version: list[str] = field(default_factory=list)
    checks: list[dict[str, str]] = field(default_factory=list)
    failed_units: list[str] = field(default_factory=list)


def parse_guest_output(output: str) -> GuestReport:
    report = GuestReport()
    for line in output.splitlines():
        start = line.find(MARKER)
        if start < 0:
            continue
        kind, _, rest = line[start + len(MARKER) :].strip().partition(" ")
        rest = rest.strip()
        if kind == "DONE":
            report.done = True
        elif kind == "STATE":
            report.state = rest
        elif kind == "INFO":
            key, _, value = rest.partition(" ")
            if key == "kernel":
                report.kernel = value
            elif key == "zfs-version" and value:
                report.zfs_version.append(value)
        elif kind == "CHECK":
            name, _, outcome = rest.partition(" ")
            status, _, detail = outcome.partition(" ")
            report.checks.append({"name": name, "status": status, "detail": detail})
        elif kind == "FAILED-UNIT" and rest:
            report.failed_units.append(rest)
    return report


def judge(report: GuestReport) -> tuple[bool, str]:
    """(passed, one-line summary). Every check must have run and passed."""
    if not report.done:
        return False, "The VM never reached the point where the checks finish (no SSH, or the checks hung)."
    ran = {check["name"]: check for check in report.checks}
    missing = [name for name, _ in GUEST_CHECKS if name not in ran]
    failed = [name for name, check in ran.items() if check["status"] != "pass"]
    problems = []
    if missing:
        problems.append("checks with no result: " + ", ".join(missing))
    if failed:
        problems.append("failed checks: " + ", ".join(failed))
    if problems:
        return False, "Booted and logged in, but " + "; ".join(problems) + "."
    return True, "Booted, logged in over SSH, ZFS is installed and its module loaded at boot."


def pinned_reference(image: str, digest: str) -> str:
    """`image` with its tag (or digest) replaced by `digest`."""
    repository = image.split("@", 1)[0]
    name_start = repository.rfind("/") + 1
    tag_colon = repository.rfind(":")
    if tag_colon >= name_start:
        repository = repository[:tag_colon]
    return f"{repository}@{digest}"


def write_units(directory: Path) -> Path:
    """The --systemd-units directory bcvk injects: see ROOTHOME_UNIT."""
    system = directory / "system"
    wants = system / "default.target.wants"
    wants.mkdir(parents=True, exist_ok=True)
    (system / "zac-roothome.service").write_text(ROOTHOME_UNIT, encoding="utf-8")
    for name, target in (
        ("zac-roothome.service", "../zac-roothome.service"),
        ("sshd.service", "/usr/lib/systemd/system/sshd.service"),
    ):
        link = wants / name
        if not link.is_symlink():
            link.symlink_to(target)
    return directory


def boot_command(
    image: str, *, units: Path, script_dir: Path, log_dir: Path, name: str
) -> list[str]:
    # Every path is absolute: in a local run a relative --systemd-units path was
    # silently ignored, and the VM booted without sshd.
    return [
        "bcvk", "ephemeral", "run-ssh",
        "--name", name,
        "--memory", VM_MEMORY,
        "--vcpus", VM_VCPUS,
        "--systemd-units", str(units.resolve()),
        "--ro-bind", f"{script_dir.resolve()}:{GUEST_SCRIPT_MOUNT}",
        "--log-dir", f"journal,console={log_dir.resolve()}/",
        image,
        "--",
        f"bash /run/virtiofs-mnt-{GUEST_SCRIPT_MOUNT}/checks.sh",
    ]  # fmt: skip


def boot(image: str, work: Path, attempt: int) -> tuple[GuestReport, str]:
    """Boot `image` once and run the checks. Returns the report and bcvk's output."""
    log_dir = work / f"boot-{attempt}"
    log_dir.mkdir(parents=True, exist_ok=True)
    units = write_units(work / "units")
    # The script travels as a file on a read-only virtiofs mount rather than
    # inside the SSH command line, so what the guest ran is in the artifact.
    script_dir = work / "checks"
    script_dir.mkdir(exist_ok=True)
    (script_dir / "checks.sh").write_text(guest_script(), encoding="utf-8")
    name = f"zac-boot-test-{os.getpid()}-{attempt}"
    command = boot_command(image, units=units, script_dir=script_dir, log_dir=log_dir, name=name)
    print("+ " + shlex.join(command), flush=True)
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
            timeout=BOOT_TIMEOUT_SECONDS,
        )
        output = proc.stdout + proc.stderr
    except subprocess.TimeoutExpired as exc:
        # TimeoutExpired carries bytes even with text=True. Interpolated as-is,
        # they become a b'...' repr with escaped newlines, the marker lines are
        # lost, and a boot whose checks ran and failed looks like one that never
        # ran them, which earns a retry that could pass.
        partial = "".join(
            part.decode(errors="replace") if isinstance(part, bytes) else (part or "")
            for part in (exc.stdout, exc.stderr)
        )
        output = partial + f"\nbcvk timed out after {BOOT_TIMEOUT_SECONDS}s"
    finally:
        # Killing bcvk leaves the VM's container running, holding its memory
        # through the next attempt.
        subprocess.run(
            ["podman", "rm", "--force", "--ignore", name], capture_output=True, check=False
        )
    (log_dir / "bcvk.log").write_text(output, encoding="utf-8")
    return parse_guest_output(output), output


def render_summary(result: dict, output: str) -> str:
    """The step summary for one run: `result` is what result.json holds."""
    report = result["report"]
    lines = [
        "## Boot test",
        "",
        f"**{'Passed' if result['passed'] else 'Failed'}**: {result['summary']}",
        "",
        "| | |",
        "|---|---|",
        f"| Image | `{result['image']}` |",
        f"| Booted | `{result['booted']}` |",
        f"| Boots | {result['attempts']} |",
        f"| Kernel | `{report['kernel'] or 'unknown'}` |",
        f"| systemd state | `{report['state'] or 'unknown'}` |",
    ]
    if report["zfs_version"]:
        versions = " / ".join(f"`{v}`" for v in report["zfs_version"])
        lines.append(f"| `zfs version` | {versions} |")
    if report["checks"]:
        lines += ["", "| Check | Result | Detail |", "|---|---|---|"]
        lines += [f"| `{c['name']}` | {c['status']} | `{c['detail']}` |" for c in report["checks"]]
    if report["failed_units"]:
        # Reported, not judged: several units fail on every ephemeral boot for
        # reasons that are the VM's, such as bootupd finding no /boot.
        units = ", ".join(f"`{u}`" for u in report["failed_units"])
        lines += ["", f"Failed units (not judged): {units}"]
    if not result["passed"]:
        tail = "\n".join(output.splitlines()[-LOG_TAIL_LINES:])
        lines += ["", "<details><summary>bcvk output (tail)</summary>", "", "```", tail, "```"]
        lines += ["", "</details>"]
    return "\n".join(lines) + "\n"


def main() -> None:
    image = require_env("BOOT_TEST_IMAGE")
    work = Path(optional_env("BOOT_TEST_WORK") or "artifacts/boot-test")
    work.mkdir(parents=True, exist_ok=True)

    # Boot a digest, not the tag: `:latest` can move while this runs, and the
    # summary must name exactly the artifact that was booted.
    digest = skopeo_inspect_digest(f"docker://{image}")
    pinned = pinned_reference(image, digest)
    run_cmd_with_retries(["podman", "pull", "--quiet", pinned], timeout=PULL_TIMEOUT_SECONDS)

    report, output, attempts = GuestReport(), "", 0
    for attempt in range(1, BOOT_ATTEMPTS + 1):
        attempts = attempt
        report, output = boot(pinned, work, attempt)
        if report.done or report.checks:
            break
    passed, summary = judge(report)

    result = {
        "image": image,
        "booted": pinned,
        "passed": passed,
        "summary": summary,
        "attempts": attempts,
        "report": asdict(report),
    }
    (work / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    text = render_summary(result, output)
    print(text, flush=True)
    summary_path = optional_env("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(text)

    if not passed:
        raise CiToolError(f"Boot test failed for {pinned}: {summary}")


if __name__ == "__main__":
    main()

"""
Script: tests/test_action_prepare_rechunk_host.py
What: Executes the two shell steps of .github/actions/prepare-rechunk-host/action.yml -- the
version-matched podman/crun installation and the relocation of container storage onto the
runner's large disk.
Doing: Extracts each step's `run:` body with PyYAML and runs it under bash against a `sudo` stub
that records what it was asked to do without doing it, and a `podman`/`crun` pair of stubs whose
reported versions the test controls.
Why: Both steps exist because of production failures, and no tier executes either.
tests/test_workflow_build_container.py text-matches workflow YAML, tests/e2e/ dispatches
`ci_tools.cli` commands rather than workflow steps, and tests/check_coverage.py excludes
composite actions from its manifest by design. The install step's own comment records run
30535305788, where a runner image bumped podman 4.9.3 -> 5.8.4 without bumping crun and Chunkah
died with `crun: unknown version specified`; the fail-closed podman-major check that was added in
response is a branch nothing has ever taken. The relocation step's `rm -rf` before its `ln -s` is
what keeps the symlink from being created *inside* a pre-existing storage directory, which would
leave container storage on the small root disk while the job reported success.
Goal: Make a regression in the toolchain pin or in the storage relocation fail here, rather than
as `crun: unknown version specified` or an out-of-space error deep inside a build.

The steps' text is executed rather than copied. A renamed or deleted step fails the extraction
loudly instead of leaving this file asserting nothing.

Nothing here installs a package or touches `/mnt`. `sudo` is a stub that records and returns;
`podman` and `crun` are stubs; PATH deliberately omits the directories where the real tools live.
`HOME` is redirected into a temporary directory, so the storage step's `rm -rf` and `ln -s`
operate only on scratch files.

PyYAML is not a pytest dependency; CI installs it by name (see .github/workflows/test.yml).
The import is guarded so the suite still runs under `python3 -m unittest discover -s tests`
with nothing installed, matching tests/test_action_publish_native_image.py.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - exercised only where PyYAML is absent
    yaml = None

REPO_ROOT = Path(__file__).resolve().parent.parent
ACTION_PATH = REPO_ROOT / ".github" / "actions" / "prepare-rechunk-host" / "action.yml"

INSTALL_STEP = "Update Podman"
STORAGE_STEP = "Move container storage to the large runner disk"

# PATH must reach the stubs and must NOT reach a real podman or sudo, which live in /usr/bin on a
# runner. Only the stub directory plus a shell is offered, so an unstubbed privileged call fails
# with "command not found" instead of running.
SAFE_PATH = "/usr/bin:/bin"

# The relocation target is hard-coded in the step and is not configurable from outside.
STORAGE_TARGET = "/mnt/containers"

# Records the privileged command and does not run it. `cat` drains stdin so the `echo | sudo tee`
# pipeline sees a reader; the text it was piped is recorded, since that line is the apt source
# that decides which suite the toolchain comes from.
SUDO_STUB = r"""#!/bin/sh
piped=$(cat)
{
  printf 'CMD\tsudo\n'
  printf 'STDIN\t%s\n' "${piped}"
  for arg in "$@"; do
    printf 'ARG\t%s\n' "${arg}"
  done
  printf 'END\n'
} >> "${STUB_LOG}"
exit 0
"""

# Reports whatever version the test asks for, in the shape the real tools print, and records the
# call so "both versions were logged for diagnosability" is checkable.
VERSION_STUB = r"""#!/bin/sh
tool=$(basename "$0")
{
  printf 'CMD\t%s\n' "${tool}"
  printf 'STDIN\t\n'
  for arg in "$@"; do
    printf 'ARG\t%s\n' "${arg}"
  done
  printf 'END\n'
} >> "${STUB_LOG}"
case "${tool}" in
  podman) printf '%s\n' "podman version ${STUB_PODMAN_VERSION}" ;;
  crun) printf '%s\n' "crun version ${STUB_CRUN_VERSION}" ;;
esac
exit 0
"""


def _action() -> dict:
    return yaml.safe_load(ACTION_PATH.read_text(encoding="utf-8"))


def _step(name: str) -> dict:
    """
    Return the composite step called `name`.

    Located by name rather than by position so a step inserted above it cannot silently move
    these tests onto different shell.
    """

    for step in _action()["runs"]["steps"]:
        if step.get("name") == name:
            return step
    raise AssertionError(
        f"prepare-rechunk-host/action.yml has no step named {name!r}; this test executes that "
        "step's shell and cannot find it"
    )


def _step_body(name: str) -> str:
    body = _step(name).get("run")
    if not body:
        raise AssertionError(
            f"prepare-rechunk-host/action.yml step {name!r} no longer has a `run:` body"
        )
    return body


class Invocation:
    """One recorded call to a stubbed tool: the tool, its argv, and anything piped into it."""

    def __init__(self, tool: str, fields: list[tuple[str, str]]):
        self.name = tool
        self.argv = [value for key, value in fields if key == "ARG"]
        self.stdin = next((value for key, value in fields if key == "STDIN"), "")


class Result:
    """
    What a step leaves behind: its exit status, its output, every stubbed call, and the state
    of the storage path.

    The storage path is inspected while the temporary `HOME` still exists and recorded here,
    because the directory is removed before any assertion runs.
    """

    def __init__(self, completed: subprocess.CompletedProcess, log: str, home: Path):
        self.returncode = completed.returncode
        self.stdout = completed.stdout
        self.stderr = completed.stderr
        self.calls = _parse_log(log)
        storage = home / ".local" / "share" / "containers"
        self.storage_is_symlink = storage.is_symlink()
        self.storage_target = os.readlink(storage) if self.storage_is_symlink else None
        # `ln -s DIR EXISTING_DIR` lands here instead of replacing the directory. Checked with
        # is_symlink() rather than exists(), which would follow the link to an absent /mnt.
        self.storage_has_nested_link = (storage / "containers").is_symlink()

    def named(self, name: str) -> list[Invocation]:
        return [call for call in self.calls if call.name == name]

    @property
    def sudo_argv(self) -> list[list[str]]:
        return [call.argv for call in self.named("sudo")]


def _parse_log(log: str) -> list[Invocation]:
    """
    Split the stub log into one Invocation per recorded call.

    Each block starts with a `CMD` line naming the tool and ends with a bare `END`, so a piped
    value cannot be mistaken for the start of the next call.
    """

    calls: list[Invocation] = []
    fields: list[tuple[str, str]] = []
    tool = ""
    for line in log.split("\n"):
        if line == "END":
            calls.append(Invocation(tool, fields))
            fields = []
            tool = ""
            continue
        if "\t" not in line:
            continue
        key, value = line.split("\t", 1)
        if key == "CMD":
            tool = value
            continue
        fields.append((key, value))
    return calls


@unittest.skipIf(yaml is None, "PyYAML is not installed")
class PrepareRechunkHostStepTests(unittest.TestCase):
    """
    The runner preparation, run as shell.

    Every case executes the action's own text; neither step has an `env:` block or reads an
    input, so the environment below is only the stub plumbing plus `HOME`.
    """

    def _run(
        self,
        step_name: str,
        *,
        podman_version: str = "5.8.4",
        crun_version: str = "1.24",
        prepare_home=None,
    ) -> Result:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bindir = root / "bin"
            bindir.mkdir()
            for name, text in (
                ("sudo", SUDO_STUB),
                ("podman", VERSION_STUB),
                ("crun", VERSION_STUB),
            ):
                stub = bindir / name
                stub.write_text(text, encoding="utf-8")
                stub.chmod(0o755)

            home = root / "home"
            home.mkdir()
            if prepare_home is not None:
                prepare_home(home)

            log = root / "log"
            log.touch()

            env = {
                "PATH": f"{bindir}:{SAFE_PATH}",
                "HOME": str(home),
                "STUB_LOG": str(log),
                "STUB_PODMAN_VERSION": podman_version,
                "STUB_CRUN_VERSION": crun_version,
            }

            completed = subprocess.run(
                ["bash", "-c", _step_body(step_name)],
                env=env,
                cwd=tmp,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            return Result(completed, log.read_text(encoding="utf-8"), home)

    # -- the version-matched toolchain -------------------------------------

    def test_the_four_container_tools_are_installed_as_one_version_matched_set(self) -> None:
        """
        crun, buildah, podman and skopeo must all be pinned to the same suite, in one command.

        This is the direct lesson of run 30535305788, recorded in the step's own comment: the
        runner image moved podman from 4.9.3 to 5.8.4 without moving crun, and Chunkah died with
        `crun: unknown version specified`. Installing the four together from `resolute` is what
        makes them move together. Dropping any one of them -- crun especially, since it is the
        one that is easy to think of as an implementation detail of podman -- reintroduces
        exactly that failure, and `--allow-downgrades` is what lets the suite win over a newer
        preinstalled package rather than the install silently becoming a no-op.
        """

        result = self._run(INSTALL_STEP)
        self.assertEqual(result.returncode, 0, result.stderr)
        installs = [argv for argv in result.sudo_argv if argv[:2] == ["apt-get", "install"]]
        self.assertEqual(len(installs), 1, result.sudo_argv)
        self.assertEqual(
            installs[0],
            [
                "apt-get",
                "install",
                "-y",
                "--allow-downgrades",
                "crun/resolute",
                "buildah/resolute",
                "podman/resolute",
                "skopeo/resolute",
            ],
        )

    def test_the_resolute_suite_is_added_and_the_index_refreshed_before_the_install(
        self,
    ) -> None:
        """
        The `resolute` suite has to exist to apt, and be visible, before the pinned install runs.

        `crun/resolute` resolves to nothing without the source line, and to a stale version
        without the refresh -- and apt reports the second case as an ordinary "no candidate"
        error, which reads like the suite was renamed upstream rather than like a missing
        `apt-get update`. Asserting the piped source line, its destination file and the ordering
        pins all three parts of a sequence whose steps are individually plausible in any order.
        """

        result = self._run(INSTALL_STEP)
        names = [
            " ".join(argv[:2]) if argv[0] == "apt-get" else argv[0]
            for argv in result.sudo_argv
        ]
        self.assertEqual(names, ["tee", "apt-get update", "apt-get install"])
        tee = result.named("sudo")[0]
        self.assertEqual(tee.argv, ["tee", "/etc/apt/sources.list.d/resolute.list"])
        self.assertEqual(
            tee.stdin,
            "deb http://azure.archive.ubuntu.com/ubuntu resolute universe main",
        )

    def test_an_installed_podman_older_than_5_fails_the_step(self) -> None:
        """
        The fail-closed check, on the version that caused the incident.

        Podman before 5 drops Chunkah's OCI layer annotations on push, silently -- the build
        would go green and ship an image that gains nothing from having been rechunked. The step
        installs unconditionally precisely so that it cannot be fooled by a preinstalled
        version, which means a regression in the `resolute` suite handing back podman 4.x is a
        real possibility rather than a hypothetical one. This branch has never been taken in
        production, so this test is the only thing that has ever executed it.
        """

        result = self._run(INSTALL_STEP, podman_version="4.9.3")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("4.x", result.stderr)
        self.assertIn("Chunkah needs >= 5", result.stderr)

    def test_a_two_digit_major_version_is_not_read_as_older_than_5(self) -> None:
        """
        The guard compares a number, and `10` must not lose to `5` as a string would.

        `grep -oE '[0-9]+' | head -n 1` extracts the major version as text and `-lt` compares it
        as an integer; a future edit to either half -- a `<` in place of `-lt`, or a pattern that
        grabs a digit rather than a run of digits -- would keep every currently passing case
        green and start failing the build on the first podman 10 release.
        """

        result = self._run(INSTALL_STEP, podman_version="10.0.1")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_both_podman_and_crun_versions_are_logged(self) -> None:
        """
        The diagnosability the incident asked for.

        The step's comment records that run 30535305788 was harder to pin down than it needed to
        be because the runner's crun version was absent from the log. Printing podman's version
        alone would look complete while leaving out the half that actually broke, and the
        version check reads podman's output for its own reasons -- so the crun line is the part
        that is easy to lose in an edit and that nothing else would notice.
        """

        result = self._run(INSTALL_STEP, podman_version="5.8.4", crun_version="1.24")
        # podman twice: once for the log line, once more for the version the guard compares.
        self.assertEqual(
            [call.argv for call in result.named("podman")], [["--version"], ["--version"]]
        )
        self.assertEqual([call.argv for call in result.named("crun")], [["--version"]])
        self.assertIn("podman version 5.8.4", result.stdout)
        self.assertIn("crun version 1.24", result.stdout)

    # -- moving container storage off the root disk ------------------------

    def test_container_storage_becomes_a_symlink_to_the_large_disk(self) -> None:
        """
        The relocation itself, on a runner that has no `~/.local/share` yet.

        Everything downstream depends on this one symlink: the build writes the image through it,
        and rechunk-native-image then keeps a second copy alongside. If the link is not created
        the job still succeeds here and fails later, out of space, in whichever step happened to
        need the last gigabyte.
        """

        result = self._run(STORAGE_STEP)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.storage_is_symlink, "~/.local/share/containers is not a symlink")
        self.assertEqual(result.storage_target, STORAGE_TARGET)

    def test_a_pre_existing_storage_directory_is_removed_rather_than_linked_into(self) -> None:
        """
        The `rm -rf` before the `ln -s`, which is the whole reason it is there.

        `ln -s /mnt/containers ~/.local/share/containers` against an *existing directory* does
        not fail and does not replace it: it creates `~/.local/share/containers/containers`
        pointing at the target, leaving podman's real storage exactly where it was, on the small
        root disk. The step would report success and the job would run out of disk later. Runner
        images do ship a populated `~/.local/share`, so this is the production shape, not a
        contrived one.
        """

        def populate(home: Path) -> None:
            existing = home / ".local" / "share" / "containers"
            existing.mkdir(parents=True)
            (existing / "storage.conf").write_text("stale\n", encoding="utf-8")

        result = self._run(STORAGE_STEP, prepare_home=populate)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(
            result.storage_has_nested_link,
            "the symlink was created inside the old storage directory instead of replacing it",
        )
        self.assertTrue(result.storage_is_symlink, "~/.local/share/containers is not a symlink")
        self.assertEqual(result.storage_target, STORAGE_TARGET)

    def test_the_target_directory_is_created_and_handed_to_the_runner_user(self) -> None:
        """
        `/mnt` is root-owned on GitHub-hosted runners, so the step must create and chown it.

        Rootless podman writes through the symlink as the unprivileged runner user; without the
        chown the very first write fails with a permission error that names the symlink rather
        than `/mnt`, which points an investigation at the wrong half of this step. The chown
        target is the *current* user, taken from `id`, not a hard-coded `runner`.
        """

        result = self._run(STORAGE_STEP)
        self.assertIn(["mkdir", "-p", STORAGE_TARGET], result.sudo_argv)
        chown = [argv for argv in result.sudo_argv if argv and argv[0] == "chown"]
        self.assertEqual(len(chown), 1, result.sudo_argv)
        self.assertRegex(chown[0][1], r"^[0-9]+:[0-9]+$")
        self.assertEqual(chown[0][2], STORAGE_TARGET)

    # -- shape of the action -----------------------------------------------

    def test_no_step_interpolates_an_expression_into_its_shell(self) -> None:
        """
        Every `${{ }}` in this action stays out of the `run:` bodies.

        This action takes no inputs today, so there is nothing to interpolate -- which is exactly
        when the pattern gets introduced without anyone thinking about it. Checked for every step
        so an input added later arrives through an `env:` block, as data, rather than as shell
        source.
        """

        for step in _action()["runs"]["steps"]:
            body = step.get("run")
            if not body:
                continue
            self.assertNotIn(
                "${{",
                body,
                f"step {step.get('name')!r} interpolates a workflow expression directly into "
                "its shell; pass the value through the step's env: block instead",
            )


if __name__ == "__main__":
    unittest.main()

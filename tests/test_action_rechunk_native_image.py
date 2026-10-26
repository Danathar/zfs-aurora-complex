"""
Script: tests/test_action_rechunk_native_image.py
What: Executes the single shell step of .github/actions/rechunk-native-image/action.yml -- the
Chunkah invocation that re-layers every image this repository ships, immediately before it is
pushed and signed.
Doing: Extracts the step's `run:` body and `env:` block with PyYAML, resolves the block's
`${{ inputs.* }}` expressions the way GitHub resolves them (refusing any expression this harness
does not model), and runs the body under bash against a `podman` stub that records each
invocation's argv one argument per line together with the environment the step exported for it.
Why: This action is the last thing that rewrites the image before publication, and no tier
executes it. tests/test_workflow_build_container.py text-matches workflow YAML,
tests/e2e/ dispatches `ci_tools.cli` commands rather than workflow steps, and
tests/check_coverage.py excludes composite actions from its manifest by design. The step encodes
four decisions that are invisible once the job is green: the source image's own config is carried
into the rechunked output via `CHUNKAH_CONFIG_STR`, container storage is pruned *before* the
archive is loaded (the whole reason the build buffers to an archive instead of piping), the load
unpacks under `/mnt` rather than the smaller root disk, and the rechunked image is re-tagged back
onto the tag the caller passed so downstream publication needs no changes. A regression in any of
them produces either a silently misconfigured image or a disk-exhaustion failure thousands of
lines into a build log.
Goal: Make a changed rechunk contract fail here rather than on a booted machine or at the point
where a runner runs out of disk.

The step's text is executed rather than copied. A renamed or deleted step, or an input this
harness does not supply, fails loudly instead of leaving this file asserting nothing.

Nothing here rechunks an image. `podman` and `sudo` are stubs and PATH deliberately omits the
directories where the real tools live, so a step that stopped being stubbed would fail rather
than pull quay.io/coreos/chunkah. `sudo` records what it was asked to do and does not run it, so
the test never touches `/mnt`.

The step buffers through the literal path `/tmp/chunkah-oci.tar`, which is not configurable from
outside; the tests below therefore use that path and assert the step removes it again.

PyYAML is not a pytest dependency; CI installs it by name (see .github/workflows/test.yml).
The import is guarded so the suite still runs under `python3 -m unittest discover -s tests`
with nothing installed, matching tests/test_action_publish_native_image.py.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
import unittest
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - exercised only where PyYAML is absent
    yaml = None

REPO_ROOT = Path(__file__).resolve().parent.parent
ACTION_PATH = REPO_ROOT / ".github" / "actions" / "rechunk-native-image" / "action.yml"

RECHUNK_STEP = "Rechunk local image with Chunkah"

# PATH must reach the stubs and must NOT reach a real podman, which lives in /usr/bin on a
# runner. Only the stub directory plus a shell is offered, so an unstubbed call fails with
# "command not found" instead of starting a container.
SAFE_PATH = "/usr/bin:/bin"

# The buffered archive path is hard-coded in the step, so it is hard-coded here too.
ARCHIVE = Path("/tmp/chunkah-oci.tar")

IMAGE_NAME = "zfs-aurora-complex"
IMAGE_TAG = "candidate-34266369977"
SOURCE_IMAGE = f"localhost/{IMAGE_NAME}:{IMAGE_TAG}"
CHUNKED_IMAGE = f"localhost/{IMAGE_NAME}:chunkah"

# What `podman inspect --format '{{json .Config}}'` hands back on a runner: the source image's
# own config object, which Chunkah reads so the rechunked output keeps the labels and environment
# of the image being re-layered.
INSPECT_OUTPUT = '{"Labels":{"org.zfs-aurora-complex.zfs-version":"2.3.4"}}'

# Stand-in for the compressed OCI archive Chunkah writes to stdout. Its only job is to be
# recognisable when `podman load` reads it back.
ARCHIVE_BYTES = "chunkah-oci-archive-contents"

# Records one block per invocation. argv goes one argument per line so an empty-valued flag is
# visible as an empty element rather than vanishing into a joined string, and the two environment
# variables the step is responsible for setting are recorded alongside it: CHUNKAH_CONFIG_STR,
# which the step exports, and TMPDIR, which it sets for `podman load` alone.
PODMAN_STUB = r"""#!/bin/sh
# What the archive actually held at load time, which is the only way to tell "loaded what
# Chunkah produced" from "loaded whatever happened to be on disk".
loaded=""
if [ "$1" = "load" ]; then
  prev=""
  for arg in "$@"; do
    if [ "${prev}" = "-i" ]; then
      loaded=$(cat "${arg}" 2>/dev/null)
    fi
    prev=${arg}
  done
fi

# The whole record, including its terminator, is written before anything can exit, so a
# non-zero subcommand cannot leave a half-written block for the next call to be appended to.
{
  printf 'CMD\tpodman\n'
  printf 'CHUNKAH_CONFIG_STR\t%s\n' "${CHUNKAH_CONFIG_STR-<unset>}"
  printf 'TMPDIR\t%s\n' "${TMPDIR-<unset>}"
  printf 'LOADED\t%s\n' "${loaded}"
  for arg in "$@"; do
    printf 'ARG\t%s\n' "${arg}"
  done
  printf 'END\n'
} >> "${STUB_LOG}"

case "$1" in
  inspect)
    printf '%s' "${STUB_INSPECT_OUTPUT}"
    ;;
  run)
    # Chunkah writes the compressed archive to stdout; the step redirects it to a file.
    printf '%s' "${STUB_ARCHIVE_BYTES}"
    exit "${STUB_RUN_EXIT:-0}"
    ;;
esac
exit 0
"""

# Records the privileged command and does not run it: the test must never create or chown a real
# /mnt directory. Reading stdin keeps a piped caller from seeing a broken pipe.
SUDO_STUB = r"""#!/bin/sh
{
  printf 'CMD\tsudo\n'
  printf 'CHUNKAH_CONFIG_STR\t<unset>\n'
  printf 'TMPDIR\t%s\n' "${TMPDIR-<unset>}"
  printf 'LOADED\t\n'
  for arg in "$@"; do
    printf 'ARG\t%s\n' "${arg}"
  done
  printf 'END\n'
} >> "${STUB_LOG}"
exit 0
"""

INPUT_EXPRESSION = re.compile(r"^\$\{\{\s*inputs\.([A-Za-z0-9_]+)\s*\}\}$")


def _action() -> dict:
    return yaml.safe_load(ACTION_PATH.read_text(encoding="utf-8"))


def _default_chunkah_image() -> str:
    return str(_action()["inputs"]["chunkah_image"]["default"])


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
        f"rechunk-native-image/action.yml has no step named {name!r}; this test executes that "
        "step's shell and cannot find it"
    )


def _step_body(name: str) -> str:
    body = _step(name).get("run")
    if not body:
        raise AssertionError(
            f"rechunk-native-image/action.yml step {name!r} no longer has a `run:` body"
        )
    return body


def _step_env(name: str, inputs: dict[str, str]) -> dict[str, str]:
    """
    Resolve the step's own `env:` block against `inputs`.

    The environment the shell sees is built from the action's text, not from a copy of it, so a
    renamed environment variable or a newly wired input reaches the shell here exactly as it
    would on a runner. Any expression other than a bare `${{ inputs.NAME }}` is rejected: this
    harness cannot honestly claim to resolve `github.*`, `env.*` or a composed expression, and
    silently passing one through as a literal would make the test assert the wrong thing.
    """

    resolved: dict[str, str] = {}
    for key, raw in (_step(name).get("env") or {}).items():
        value = str(raw)
        match = INPUT_EXPRESSION.match(value.strip())
        if not match:
            raise AssertionError(
                f"rechunk-native-image/action.yml step {name!r} sets {key}={value!r}; this test "
                "only resolves bare ${{ inputs.NAME }} expressions and must be taught the new "
                "one before it can keep executing this step"
            )
        input_name = match.group(1)
        if input_name not in inputs:
            raise AssertionError(
                f"rechunk-native-image/action.yml step {name!r} reads inputs.{input_name}, "
                "which this test does not supply"
            )
        resolved[key] = inputs[input_name]
    return resolved


class Invocation:
    """One recorded call to a stubbed tool: its argv and the environment it was given."""

    def __init__(self, tool: str, fields: list[tuple[str, str]]):
        self.argv = [value for key, value in fields if key == "ARG"]
        self.chunkah_config_str = next(
            (value for key, value in fields if key == "CHUNKAH_CONFIG_STR"), "<unset>"
        )
        self.tmpdir = next((value for key, value in fields if key == "TMPDIR"), "<unset>")
        self.loaded = next((value for key, value in fields if key == "LOADED"), None)
        # A short label: `sudo`, or podman plus its subcommand -- two words for `image prune`,
        # which is the only two-word podman subcommand this step uses.
        if tool == "sudo":
            self.name = "sudo"
        elif not self.argv:
            self.name = "podman"
        elif self.argv[0] == "image":
            self.name = "podman " + " ".join(self.argv[:2])
        else:
            self.name = f"podman {self.argv[0]}"


class Result:
    """What the step leaves behind: its exit status, its output, and every stubbed call."""

    def __init__(self, completed: subprocess.CompletedProcess, log: str):
        self.returncode = completed.returncode
        self.stdout = completed.stdout
        self.stderr = completed.stderr
        self.calls = _parse_log(log)

    def named(self, name: str) -> list[Invocation]:
        return [call for call in self.calls if call.name == name]

    def only(self, name: str) -> Invocation:
        matches = self.named(name)
        if len(matches) != 1:
            raise AssertionError(
                f"expected exactly one {name!r} call, recorded {len(matches)}: "
                f"{[call.name for call in self.calls]}"
            )
        return matches[0]


def _parse_log(log: str) -> list[Invocation]:
    """
    Split the stub log into one Invocation per recorded call.

    Each block starts with a `CMD` line naming the tool and ends with a bare `END`, so a value
    containing a newline cannot be mistaken for the start of the next call.
    """

    calls: list[Invocation] = []
    fields: list[tuple[str, str]] = []
    tool = "podman"
    for line in log.split("\n"):
        if line == "END":
            calls.append(Invocation(tool, fields))
            fields = []
            tool = "podman"
            continue
        if "\t" not in line:
            continue
        key, value = line.split("\t", 1)
        if key == "CMD":
            tool = "sudo" if value == "sudo" else "podman"
            continue
        fields.append((key, value))
    return calls


@unittest.skipIf(yaml is None, "PyYAML is not installed")
class RechunkActionStepTests(unittest.TestCase):
    """
    The Chunkah re-layering, run as shell.

    Every case executes the action's own text with its `env:` block resolved the way GitHub
    resolves it.
    """

    def _run(
        self,
        *,
        inputs: dict[str, str] | None = None,
        run_exit: int = 0,
    ) -> Result:
        supplied = {
            "image_name": IMAGE_NAME,
            "image_tag": IMAGE_TAG,
            "chunkah_image": _default_chunkah_image(),
        }
        supplied.update(inputs or {})

        # The step buffers to a fixed path; make sure a previous run cannot be mistaken for
        # this one's output.
        ARCHIVE.unlink(missing_ok=True)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bindir = root / "bin"
            bindir.mkdir()
            for name, text in (("podman", PODMAN_STUB), ("sudo", SUDO_STUB)):
                stub = bindir / name
                stub.write_text(text, encoding="utf-8")
                stub.chmod(0o755)

            log = root / "log"
            log.touch()

            env = {
                "PATH": f"{bindir}:{SAFE_PATH}",
                "HOME": str(root),
                "STUB_LOG": str(log),
                "STUB_INSPECT_OUTPUT": INSPECT_OUTPUT,
                "STUB_ARCHIVE_BYTES": ARCHIVE_BYTES,
                "STUB_RUN_EXIT": str(run_exit),
            }
            env.update(_step_env(RECHUNK_STEP, supplied))

            completed = subprocess.run(
                ["bash", "-c", _step_body(RECHUNK_STEP)],
                env=env,
                cwd=tmp,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            return Result(completed, log.read_text(encoding="utf-8"))

    def tearDown(self) -> None:
        ARCHIVE.unlink(missing_ok=True)

    # -- the Chunkah invocation --------------------------------------------

    def test_chunkah_receives_the_whole_documented_rechunk_contract(self) -> None:
        """
        The full `podman run` argv, pinned as one list.

        Each element changes what the published image is: `--mount=type=image` is what makes
        Chunkah read the image this build produced rather than a registry copy; `--max-layers
        128` is the layering budget; `--prune /sysroot/` drops the ostree sysroot that must not
        be re-layered; the two `--label ...-` removals strip the ostree commit labels that would
        otherwise be stale on the rechunked copy; and `--tag` names the intermediate image every
        later command in this step refers to. Asserting the list rather than a handful of
        substrings means a flag that is dropped, reordered onto the wrong value, or quietly
        added shows up here.
        """

        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.only("podman run").argv,
            [
                "run",
                "--rm",
                f"--mount=type=image,src={SOURCE_IMAGE},target=/chunkah",
                "-e",
                "CHUNKAH_CONFIG_STR",
                _default_chunkah_image(),
                "build",
                "--verbose",
                "--compressed",
                "--max-layers",
                "128",
                "--prune",
                "/sysroot/",
                "--label",
                "ostree.commit-",
                "--label",
                "ostree.final-diffid-",
                "--tag",
                CHUNKED_IMAGE,
            ],
        )

    def test_the_source_images_own_config_is_carried_into_chunkah(self) -> None:
        """
        `CHUNKAH_CONFIG_STR` must hold the inspect output for the image being rechunked.

        Chunkah starts from a blank image config unless it is handed one, so an empty or
        wrong-image value here produces an image that boots but has lost its labels and
        environment -- including the provenance labels build-native-image took care to set. The
        step exports the variable and passes it by name with `-e`, two places that can drift
        apart; this pins both ends to the same `podman inspect` and pins which image was
        inspected.

        The inspect is narrowed to `.Config` with `--format '{{json .Config}}'`. The action's
        comment records why: a full `podman inspect` carries per-layer data that grows with the
        base image, and once the Aurora base reached 256 layers the exported value passed Linux's
        128 KiB cap on a single environment string and `podman run` failed with E2BIG. Dropping
        the `--format` brings that failure back on the next large base.
        """

        result = self._run()
        self.assertEqual(
            result.only("podman inspect").argv,
            ["inspect", "--format", "{{json .Config}}", SOURCE_IMAGE],
        )
        self.assertEqual(result.only("podman run").chunkah_config_str, INSPECT_OUTPUT)

    def test_the_chunkah_image_default_is_digest_pinned(self) -> None:
        """
        The default Chunkah reference must carry a digest, not just a version tag.

        This step rewrites the image immediately before it is pushed and signed, so a moved tag
        would let unreviewed content in right before this repository's own key signs it -- the
        reason the action's own comment gives for pinning. Renovate keeps the version and digest
        current; nothing but this test notices if the digest half is ever dropped.
        """

        self.assertRegex(_default_chunkah_image(), r"^\S+:v[0-9][^@\s]*@sha256:[0-9a-f]{64}$")

    # -- the disk-headroom sequence ----------------------------------------

    def test_storage_is_pruned_before_the_archive_is_loaded(self) -> None:
        """
        The ordering that the buffered archive exists to permit.

        The action's comment is explicit that piping Chunkah straight into `podman load` would
        need the source image and the rechunked copy unpacked in one container store at once,
        roughly twice the image size, which is what exhausts the runner's root disk. Buffering
        only helps if the prune happens between the two. An edit that moved `podman image prune`
        after the load, or dropped it, would keep every test green and fail in production as an
        out-of-space error partway through a long build.
        """

        result = self._run()
        sequence = [call.name for call in result.calls if call.name.startswith("podman")]
        self.assertEqual(
            sequence,
            [
                "podman inspect",
                "podman pull",
                "podman run",
                "podman image prune",
                "podman load",
                "podman tag",
            ],
        )
        self.assertEqual(result.only("podman image prune").argv, ["image", "prune", "-af"])

    def test_chunkah_is_pulled_explicitly_with_a_long_retry_window(self) -> None:
        """
        The Chunkah pull is its own `podman pull`, retried 30 seconds apart.

        `podman run` pulls implicitly with 3 attempts one second apart, which a quay.io CDN
        connection reset outlasts (the sister Kinoite project's run 39910817917). The explicit
        pull must name the same pinned reference the later `podman run` uses, so that run finds
        it locally.
        """

        result = self._run()
        self.assertEqual(
            result.only("podman pull").argv,
            ["pull", "--retry", "5", "--retry-delay", "30s", _default_chunkah_image()],
        )

    def test_the_load_reads_the_archive_chunkah_wrote_and_unpacks_on_the_large_disk(
        self,
    ) -> None:
        """
        `podman load` must consume Chunkah's own output, with `TMPDIR` moved off the root disk.

        Two separate guarantees, both invisible in a green job. The archive contents recorded at
        load time prove the redirection actually captured Chunkah's stdout rather than leaving an
        empty or stale file. `TMPDIR=/mnt/tmp` is where podman unpacks the archive before
        applying it; losing that prefix sends an image-sized unpack to `/var/tmp` on the smaller
        root disk, which is the failure the relocation in prepare-rechunk-host exists to avoid.
        """

        result = self._run()
        load = result.only("podman load")
        self.assertEqual(load.argv, ["load", "-i", str(ARCHIVE)])
        self.assertEqual(load.loaded, ARCHIVE_BYTES)
        self.assertEqual(load.tmpdir, "/mnt/tmp")

    def test_the_unpack_directory_is_created_and_handed_to_the_runner_user(self) -> None:
        """
        `/mnt` is root-owned on GitHub-hosted runners, so the step must create and chown it.

        Rootless podman cannot write into a root-owned `TMPDIR`; without the chown the load
        above fails with a permission error rather than a disk-space one, which is a slower thing
        to diagnose. The chown target is the *current* user, taken from `id`, not a hard-coded
        `runner`.
        """

        result = self._run()
        sudo = [call.argv for call in result.calls if call.name == "sudo"]
        self.assertIn(["mkdir", "-p", "/mnt/tmp"], sudo)
        chown = [argv for argv in sudo if argv and argv[0] == "chown"]
        self.assertEqual(len(chown), 1, sudo)
        self.assertRegex(chown[0][1], r"^[0-9]+:[0-9]+$")
        self.assertEqual(chown[0][2], "/mnt/tmp")

    def test_the_buffered_archive_is_removed_once_it_has_been_loaded(self) -> None:
        """
        An image-sized file left on the root disk would undo the headroom this step just made.

        The rechunked image is loaded into container storage by this point, so the archive is
        dead weight -- and the steps that follow in the calling workflow push and sign an image
        whose size is bounded by the same disk.
        """

        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(
            ARCHIVE.exists(), f"{ARCHIVE} survived the step that is supposed to remove it"
        )

    # -- handing the result back -------------------------------------------

    def test_the_rechunked_image_is_retagged_onto_the_tag_the_caller_passed(self) -> None:
        """
        The seam between this action and publication.

        Downstream steps -- publish-native-image and the signing that follows -- address the
        image as `image_name:image_tag` and know nothing about the `:chunkah` intermediate. If
        this final retag were dropped or pointed at the wrong side, the workflow would push and
        sign the *pre-rechunk* image that is still in storage under that tag, and every test in
        this repository would stay green while the shipped image silently stopped being chunked.
        """

        result = self._run()
        self.assertEqual(
            result.only("podman tag").argv,
            ["tag", CHUNKED_IMAGE, f"{IMAGE_NAME}:{IMAGE_TAG}"],
        )

    def test_a_failed_chunkah_run_stops_the_step_before_anything_is_pruned(self) -> None:
        """
        `set -euo pipefail` in front of a sequence whose second command destroys the source.

        `podman image prune -af` deletes the image this build produced. If Chunkah's failure did
        not stop the step, the prune would run anyway, the load would have nothing to apply, and
        the retag would fail against an empty store -- turning a readable Chunkah error into a
        confusing one after the only copy of the built image had been thrown away.
        """

        result = self._run(run_exit=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.named("podman image prune"), [])
        self.assertEqual(result.named("podman load"), [])
        self.assertEqual(result.named("podman tag"), [])

    # -- input handling ----------------------------------------------------

    def test_no_step_interpolates_an_expression_into_its_shell(self) -> None:
        """
        Every `${{ }}` in this action stays in an `env:` block, never in a `run:` body.

        `image_tag` arrives as a workflow input, so it is text this action does not control. Expanded by the runner into the
        script text it would be shell source; read from the environment it is data. This is the
        static half of the guarantee, checked for every step so a step added later cannot
        reintroduce the pattern.
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

    def test_a_shell_metacharacter_in_a_tag_stays_one_literal_argument(self) -> None:
        """
        The dynamic half: a hostile-looking tag reaches podman unexecuted and unsplit.

        Git allows `;`, `$` and `(` in a branch name, so a tag built from one can carry them.
        The value is pasted into three separate shell variables here -- the source reference, the
        mount specification and the final retag -- and each must arrive as a single argv element
        with its characters intact.
        """

        hostile = "branch;$(touch pwned)-tag"
        result = self._run(inputs={"image_tag": hostile})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.only("podman inspect").argv[-1], f"localhost/{IMAGE_NAME}:{hostile}"
        )
        self.assertEqual(
            result.only("podman tag").argv[2], f"{IMAGE_NAME}:{hostile}"
        )
        # The command substitution reached podman as text. Had the shell run it, the reference
        # would have been split at the `;` into a truncated argument and a second command.
        self.assertNotIn("pwned", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()

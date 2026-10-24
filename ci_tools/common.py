"""
Script: ci_tools/common.py
What: Shared helper functions used by all `ci_tools` modules.
Doing: Wraps env reads, command execution, image inspect/copy calls, parsing, and output writes.
Why: Avoids duplicated helper code.
Goal: Keep behavior consistent across all helper modules.
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import re
import subprocess
import tempfile
import time
import uuid
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path

# `redact_command_args` and its flag list live in `shared/` because the
# image-build ZFS helper redacts its failed commands too, and can import only
# from there. Every `ci_tools` caller keeps importing it from this module.
from shared.command_args import redact_command_args
from shared.kernel_release import kernel_release_sort_key


class CiToolError(RuntimeError):
    """Raised when a workflow helper script hits a known error condition."""


FEDORA_FROM_KERNEL_RE = re.compile(r".*fc([0-9]+).*")
REPO_ROOT = Path(__file__).resolve().parent.parent
REPO_DEFAULTS_FILE = REPO_ROOT / "ci" / "defaults.json"
# Wall-clock ceilings for external commands, in seconds.
#
# These exist to turn a hung child process into a fast, readable failure --
# not to police normal runtimes. Every value is far above what these commands
# actually take in CI, so a healthy run never approaches one. Without them the
# only backstop is the job's `timeout-minutes`, which bounds the whole job
# rather than the one command, so a stalled TLS handshake against ghcr.io
# holds a privileged job and its package-write token open until then.
#
# Commands whose runtime scales with hardware and network throughput -- the
# `just build`/`push` image builds, `dnf5`/`depmod` inside the image build --
# deliberately get no per-command ceiling here, because any number would be a
# guess. Those are covered by each workflow job's `timeout-minutes`.
REGISTRY_METADATA_TIMEOUT = 120.0
"""`skopeo inspect`: reads a manifest, transfers no layers."""

REGISTRY_TRANSFER_TIMEOUT = 1800.0
"""`skopeo copy` and `podman pull`: full layer transfer, retried."""

# How a transient registry read is retried. `skopeo copy` gets this from
# `--retry-times`; anything else goes through `run_cmd_with_retries` below.
#
# Three attempts and a fixed five-second wait are chosen against what actually
# fails here: a CDN truncating one blob mid-transfer. That either clears on the
# next attempt or is an outage no retry budget survives, so a longer ladder buys
# nothing and delays the real failure. The delay stays a constant rather than a
# backoff for the same reason.
REGISTRY_RETRY_ATTEMPTS = 3
REGISTRY_RETRY_DELAY_SECONDS = 5.0

GIT_REMOTE_TIMEOUT = 120.0
"""`git ls-remote`: ref listing only, no object transfer."""

COSIGN_TIMEOUT = 300.0
"""`cosign sign` and `cosign verify`: signature payload upload or verification."""


def require_env(name: str) -> str:
    """Return a required environment variable or raise a clear error."""
    value = os.environ.get(name)
    if value is None or value == "":
        raise CiToolError(f"Missing required environment variable: {name}")
    return value


def optional_env(name: str, default: str = "") -> str:
    """Return an environment variable with a fallback default."""
    return os.environ.get(name, default)


def registry_creds_from_env(*, required: bool = False) -> str | None:
    """
    Return the `REGISTRY_ACTOR:REGISTRY_TOKEN` pair every registry helper takes.

    With `required=True` a missing half raises exactly as `require_env` does, for
    tools that must never fall back to an anonymous pull. Otherwise the pair is
    `None` unless both halves are set, which the helpers below read as
    anonymous: half a credential is treated the same as none.
    """
    if required:
        return f"{require_env('REGISTRY_ACTOR')}:{require_env('REGISTRY_TOKEN')}"
    registry_actor = optional_env("REGISTRY_ACTOR")
    registry_token = optional_env("REGISTRY_TOKEN")
    if registry_actor and registry_token:
        return f"{registry_actor}:{registry_token}"
    return None


def load_repo_defaults() -> dict[str, str]:
    """
    Load checked-in repository defaults from `ci/defaults.json`.

    Keeping these defaults in version control makes workflow input changes
    reviewable. The workflows still pass explicit overrides when needed, but the
    default values themselves live in one file instead of being copied across
    multiple workflow files.
    """
    if not REPO_DEFAULTS_FILE.exists():
        raise CiToolError(f"Missing repository defaults file: {REPO_DEFAULTS_FILE}")

    with REPO_DEFAULTS_FILE.open("r", encoding="utf-8") as handle:
        data = json.load(handle)

    defaults: dict[str, str] = {}
    for key, value in data.items():
        defaults[str(key)] = str(value)
    return defaults


def require_env_or_default(name: str) -> str:
    """
    Return an environment variable, falling back to checked-in repo defaults.

    This keeps the Python helpers honest even if the workflow files become
    thinner over time. A command still stops with an error when the value is
    missing from both env and `ci/defaults.json`.
    """
    value = os.environ.get(name)
    if value is not None and value != "":
        return value

    default_value = load_repo_defaults().get(name, "")
    if default_value:
        return default_value

    raise CiToolError(
        f"Missing required environment variable: {name} "
        f"(and no fallback exists in {REPO_DEFAULTS_FILE})"
    )


def run_cmd(
    args: Sequence[str],
    *,
    capture_output: bool = True,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
) -> str:
    """
    Run a command and return stdout, raising a readable error on failure.

    `timeout` is a wall-clock ceiling in seconds. It defaults to `None` (wait
    forever) so callers that have no defensible bound keep their current
    behavior; callers that talk to a registry or a git remote pass one of the
    module-level `*_TIMEOUT` constants. Exceeding it raises `CiToolError`, the
    same type a nonzero exit raises, so a hang fails the job exactly the way a
    command failure already does instead of surfacing as a bare traceback.
    """
    try:
        command_env = None
        if env is not None:
            # Command-specific env overrides let helpers inject secrets or one-off
            # flags without mutating global process env for the rest of the job.
            command_env = dict(os.environ)
            command_env.update(env)
        result = subprocess.run(
            list(args),
            check=True,
            text=True,
            capture_output=capture_output,
            cwd=cwd,
            env=command_env,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        command = " ".join(redact_command_args(args))
        # Deliberately worded to share no substring with
        # `_MISSING_IMAGE_ERROR_MARKERS`. `skopeo_inspect_json_optional`
        # classifies failures by message text, and a timeout must never be
        # read as "the image does not exist" -- that would turn "we could not
        # tell" into a reuse/rebuild decision made from unknown state.
        raise CiToolError(f"Command timed out after {timeout}s: {command}") from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        details = stderr or stdout or str(exc)
        command = " ".join(redact_command_args(args))
        raise CiToolError(f"Command failed: {command}\n{details}") from exc

    if not capture_output:
        return ""
    return result.stdout


def run_cmd_with_retries(
    args: Sequence[str],
    *,
    attempts: int = REGISTRY_RETRY_ATTEMPTS,
    delay: float = REGISTRY_RETRY_DELAY_SECONDS,
    **kwargs,
) -> str:
    """
    Run a command, retrying a failure up to `attempts` times.

    For commands whose failure mode is a transient registry read rather than a
    wrong answer. `skopeo copy` does not need this -- it retries internally via
    `--retry-times` -- but `podman` has no equivalent flag on every subcommand
    that moves layers, and a truncated blob from a registry CDN can fail a
    whole build at its first step with nothing behind it.

    Retries every `CiToolError` `run_cmd` raises, including a timeout: both a
    stalled transfer and a truncated one are the same transient class here, and
    a command that fails for a non-transient reason fails the same way on every
    attempt, `attempts` times, and then raises. That is a bounded cost paid only
    on a path that was already failing.

    Deliberately not used for anything that decides state. Callers that read a
    manifest to choose between reuse and rebuild keep their single attempt, so a
    retry can never turn "we could not tell" into an answer.
    """
    if attempts < 1:
        raise ValueError(f"attempts must be at least 1, got {attempts}")

    for attempt in range(1, attempts + 1):
        try:
            return run_cmd(args, **kwargs)
        except CiToolError as exc:
            if attempt == attempts:
                raise CiToolError(f"Command failed after {attempts} attempts: {exc}") from exc
            # `run_cmd` has already redacted the command line inside `exc`.
            print(
                f"Attempt {attempt} of {attempts} failed, retrying in {delay}s: {exc}",
                flush=True,
            )
            time.sleep(delay)

    # Unreachable: the loop above either returns or raises on its last attempt.
    raise CiToolError(f"Command failed after {attempts} attempts")


def git_ls_remote_resolve(repo_url: str, ref: str) -> str:
    """
    Resolve a git ref (branch, tag, or SHA) on a remote repository to a concrete commit SHA.

    Used by the input resolver to float the akmods tracking ref to a pinned SHA
    before the clone step runs. Keeping the resolution here (not in the clone
    helper) preserves the `rev-parse HEAD` SHA-verification invariant in
    `akmods_clone_pinned`, which assumes its input is already a concrete SHA.
    """
    if not repo_url:
        raise CiToolError("git_ls_remote_resolve requires a non-empty repo_url")
    if not ref:
        raise CiToolError("git_ls_remote_resolve requires a non-empty ref")

    output = run_cmd(
        ["git", "ls-remote", "--exit-code", repo_url, ref], timeout=GIT_REMOTE_TIMEOUT
    )
    matches: dict[str, str] = {}
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        sha, _tab, name = line.partition("\t")
        sha = sha.strip()
        name = name.strip()
        if len(sha) == 40 and all(c in "0123456789abcdef" for c in sha):
            matches.setdefault(name, sha)

    # An annotated tag lists two lines: the tag object itself and a peeled
    # `^{}` line that points at the underlying commit. Prefer the peeled commit
    # so the returned SHA matches what `git checkout` resolves later in
    # `akmods_clone_pinned`; otherwise its `rev-parse HEAD` check would fail with
    # a spurious "ref mismatch" because HEAD lands on the commit, not the tag.
    for preferred_name in (
        f"refs/tags/{ref}^{{}}",
        f"refs/heads/{ref}",
        f"refs/tags/{ref}",
        ref,
        "HEAD",
    ):
        if preferred_name in matches:
            return matches[preferred_name]

    # Fall back to the first line's SHA if the name didn't match a known form.
    first = output.strip().splitlines()[0] if output.strip() else ""
    sha = first.split("\t", 1)[0].strip() if first else ""
    if len(sha) == 40 and all(c in "0123456789abcdef" for c in sha):
        return sha
    raise CiToolError(f"git ls-remote did not return a resolvable commit SHA for {ref} at {repo_url}")


def run_json_cmd(args: Sequence[str], *, timeout: float | None = None) -> dict:
    """Run a command that returns JSON and parse it."""
    output = run_cmd(args, timeout=timeout)
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        command = " ".join(redact_command_args(args))
        raise CiToolError(f"Expected JSON from command: {command}") from exc


def write_github_outputs(values: Mapping[str, str]) -> None:
    """
    Write step outputs for GitHub Actions.

    GitHub provides a file path in `GITHUB_OUTPUT`; writing `name=value` lines
    there makes that value available to later steps in the same job.
    """
    output_file = require_env("GITHUB_OUTPUT")
    with open(output_file, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            _write_github_file_value(handle, key, value)


def write_github_env(values: Mapping[str, str]) -> None:
    """
    Export environment variables for later GitHub Actions steps.

    GitHub exposes the file path through `GITHUB_ENV`. Writing `NAME=value`
    lines there makes the variable available to subsequent steps in the same
    job.
    """
    env_file = require_env("GITHUB_ENV")
    with open(env_file, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            _write_github_file_value(handle, key, value)


def _write_github_file_value(handle, key: str, value: str) -> None:
    delimiter = f"EOF_{uuid.uuid4().hex}"
    while delimiter in value:
        delimiter = f"EOF_{uuid.uuid4().hex}"
    handle.write(f"{key}<<{delimiter}\n{value}\n{delimiter}\n")


def normalize_owner(owner: str) -> str:
    """
    Normalize a GitHub owner/org for container image paths.

    Here, "normalize" means converting to lowercase.
    Example: `Danathar` becomes `danathar`, so image refs are consistent:
    `ghcr.io/danathar/...`.
    """
    return owner.lower()


# `skopeo` transports that never reach a registry. A credential is meaningless
# for these, so a reference using one contributes no entry to the auth file
# below -- a `dir:` copy destination must not invent a registry named `dir:`.
_LOCAL_TRANSPORT_PREFIXES = (
    "dir:",
    "oci:",
    "oci-archive:",
    "docker-archive:",
    "docker-daemon:",
    "containers-storage:",
    "tarball:",
)
_REGISTRY_TRANSPORT_PREFIX = "docker://"
# What Docker's own config file calls Docker Hub. Only reachable if a caller
# passes a short reference; every reference this repo builds names ghcr.io.
_DOCKER_HUB_AUTH_KEY = "https://index.docker.io/v1/"


def registry_host(image_ref: str) -> str:
    """
    Return the registry hostname an image reference authenticates against.

    Returns `""` when the reference is not a registry read or write, so
    `registry_auth_dir` can build an auth file for the registry end of a copy
    without inventing an entry for the local end.
    """
    if image_ref.startswith(_REGISTRY_TRANSPORT_PREFIX):
        remainder = image_ref[len(_REGISTRY_TRANSPORT_PREFIX) :]
    elif image_ref.startswith(_LOCAL_TRANSPORT_PREFIXES):
        return ""
    else:
        remainder = image_ref

    host, separator, _path = remainder.partition("/")
    if not separator:
        return _DOCKER_HUB_AUTH_KEY
    if host != "localhost" and "." not in host and ":" not in host:
        # `library/fedora`: the first segment is a Docker Hub namespace, not a host.
        return _DOCKER_HUB_AUTH_KEY
    return host


def registry_auth_file(auth_dir: str) -> str:
    """Return the `config.json` path inside a `registry_auth_dir` directory."""
    return str(Path(auth_dir) / "config.json")


@contextlib.contextmanager
def registry_auth_dir(creds: str | None, *image_refs: str) -> Iterator[str]:
    """
    Yield a directory holding a `config.json` carrying `creds`, or `""`.

    This is how a registry credential reaches `skopeo` and `cosign` without
    ever appearing in a command line. `/proc/<pid>/cmdline` is mode 0444, so a
    token passed as `--creds` or `--registry-password` is readable by *every*
    uid on the runner for as long as the child lives. A `0600` file in a
    per-call temporary directory narrows that to the uid that created it, for
    the duration of one command. That is what gotcha 6 in
    `docs/signing-and-bootc.md` ("do not pass registry secrets in command
    argv") asks for.

    What this deliberately does not claim is protection from a hostile process
    running as the *same* uid in this job. Such a process can read the
    auth-file path out of argv and open the file -- but it does not need to:
    the credential reaches these helpers as `REGISTRY_TOKEN` in the calling
    step's environment (`.github/actions/prepare-main-akmods/action.yml`), and
    `/proc/<pid>/environ` is mode 0400, i.e. readable by that same uid. No
    credential-transfer mechanism available inside a job fixes that; the
    job-level `docker/login-action` alternative is weaker on this exact axis,
    since `docker login` leaves the token in `~/.docker/config.json` for the
    whole job instead of one command. What changes here is the cross-uid
    exposure, which was real and is now gone.

    One directory serves both tools because both read the ordinary
    Docker/containers auth format: `skopeo` takes the file path through
    `--authfile` / `--src-authfile` / `--dest-authfile`, and `cosign` reads
    `config.json` out of the directory named by `DOCKER_CONFIG`.

    Yields `""` when `creds` is empty, so the anonymous callers keep their
    exact current behavior: no flag, no `DOCKER_CONFIG` override, and
    therefore whatever `docker/login-action` already left in the job.

    Raises `CiToolError` when `creds` is given but no reference names a
    registry. Writing `{"auths": {}}` there would yield a truthy directory, so
    the caller would still point `--authfile` / `DOCKER_CONFIG` at a file
    carrying no credential and the command would run anonymously with the
    credential silently unused -- the same failure mode a public package hides
    from every positive check. A caller in that state has a bug; say so.
    """
    if not creds:
        yield ""
        return

    hosts = [host for host in (registry_host(ref) for ref in image_refs) if host]
    if not hosts:
        raise CiToolError(
            "registry_auth_dir was given a credential but no registry reference to "
            f"use it against: {list(image_refs)}"
        )
    encoded = base64.b64encode(creds.encode("utf-8")).decode("ascii")
    auths = {host: {"auth": encoded} for host in dict.fromkeys(hosts)}
    with tempfile.TemporaryDirectory() as auth_dir:
        # Create the file with its mode already set instead of writing it and
        # then chmod-ing: the credential must never sit on disk under the
        # default umask, not even for the instant between the two calls.
        descriptor = os.open(
            registry_auth_file(auth_dir), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"auths": auths}, handle)
        yield auth_dir


def skopeo_inspect_json(image_ref: str, *, creds: str | None = None) -> dict:
    """
    Return JSON metadata for one image reference.

    `skopeo` reads image metadata directly from the registry without pulling and
    running a container image.

    `creds` is an `actor:token` pair. It reaches `skopeo` as an `--authfile`,
    never as `--creds`; see `registry_auth_dir` for why.

    `--no-tags` skips the repository's tag list, a second registry request
    skopeo makes after the manifest read and fails the whole inspect on. No
    caller reads `RepoTags`, and in the sister repository a 503 on that list
    alone failed a nightly build at its first read.
    """
    with registry_auth_dir(creds, image_ref) as auth_dir:
        command = ["skopeo", "inspect", "--no-tags"]
        if auth_dir:
            command.extend(["--authfile", registry_auth_file(auth_dir)])
        command.append(image_ref)
        return run_json_cmd(command, timeout=REGISTRY_METADATA_TIMEOUT)


def skopeo_inspect_digest(image_ref: str, *, creds: str | None = None) -> str:
    """Return the image digest from `skopeo inspect` output."""
    inspect_json = skopeo_inspect_json(image_ref, creds=creds)
    digest = str(inspect_json.get("Digest") or "")
    if not digest:
        raise CiToolError(f"Missing digest in skopeo inspect output for {image_ref}")
    return digest


_MISSING_IMAGE_ERROR_MARKERS = (
    "manifest unknown",
    "name unknown",
    "not found",
)


def is_missing_image_error(message: str) -> bool:
    """True when a registry inspect failure message means the image does not exist."""
    normalized = message.lower()
    return any(marker in normalized for marker in _MISSING_IMAGE_ERROR_MARKERS)


def skopeo_inspect_json_optional(image_ref: str, *, creds: str | None = None) -> dict | None:
    """
    Inspect one image, returning `None` only when the image does not exist.

    Other registry failures (auth, rate limiting, network errors) still raise
    so callers do not mistake "we couldn't tell" for "it's missing" and make a
    reuse/rebuild decision from unknown state.
    """
    try:
        return skopeo_inspect_json(image_ref, creds=creds)
    except CiToolError as exc:
        if is_missing_image_error(str(exc)):
            return None
        raise


def skopeo_copy(
    source: str,
    destination: str,
    *,
    creds: str | None = None,
    retry_times: int = 3,
    preserve_digests: bool = False,
    multi_arch: str = "",
) -> None:
    """
    Copy an image between registry references using skopeo.

    `preserve_digests` and `multi_arch` are opt-in because not every caller
    wants them: `check_akmods_cache` copies into a local `dir:` layout and
    reads `manifest.json` layers directly, and a `--multi-arch=all` manifest
    list would change that file's shape. Callers that promote a tag to another
    tag in the same registry (where the destination digest must match the
    source) should pass both.

    `creds` is an `actor:token` pair. It reaches `skopeo` as an authfile path
    on each end, never as `--src-creds`/`--dest-creds`; see `registry_auth_dir`
    for why. Both ends are still authenticated whenever `creds` is given, the
    same as the flags it replaces: a promotion copies between two references in
    the same private registry and needs the credential on each side. A local
    `dir:` end simply ignores the file it is handed.
    """
    with registry_auth_dir(creds, source, destination) as auth_dir:
        command = ["skopeo", "copy", "--retry-times", str(retry_times)]
        if auth_dir:
            auth_file = registry_auth_file(auth_dir)
            command.extend(["--src-authfile", auth_file, "--dest-authfile", auth_file])
        if preserve_digests:
            command.append("--preserve-digests")
        if multi_arch:
            command.append(f"--multi-arch={multi_arch}")
        command.extend([source, destination])
        run_cmd(command, capture_output=False, timeout=REGISTRY_TRANSFER_TIMEOUT)


def cosign_verify(image_ref: str, *, key_path: str, creds: str | None = None) -> None:
    """
    Verify a cosign signature on one image reference against a public key file.

    Raises `CiToolError` (via `run_cmd`) if verification fails for any reason:
    no signature found, wrong key, or a registry/network error. Callers that
    want "not signed" to mean "treat as unusable, do not consume" should catch
    `CiToolError` around this call rather than letting it propagate.

    `creds` is an optional `username:password` pair, the same shape the skopeo
    helpers here take. Passing it matters when the signature lives on a private
    GHCR package: fetching a signature is an ordinary registry read, so an
    anonymous `cosign verify` against a private package fails with an auth
    error, which a caller that treats any failure as "unsigned" would silently
    misread as "this cache is untrustworthy" and rebuild forever. Supplying
    credentials explicitly also avoids depending on an ambient `docker login`
    having happened earlier in the same job.

    The credential is written to a `0600` auth file and pointed at with
    `DOCKER_CONFIG`, not passed as `--registry-username`/`--registry-password`;
    see `registry_auth_dir` for why. The env override is scoped to this one
    command, so a job that authenticated with `docker/login-action` and calls
    this without credentials still resolves them from its own Docker config
    exactly as before.

    Deliberately does not pass `--new-bundle-format=false`, unlike
    `sign_image.py`'s sign step. This function runs in more than one
    environment -- including cosign v2.4.1, preinstalled in the
    `ghcr.io/ublue-os/devcontainer` akmods build container, which does not
    recognize that flag at all -- so it must work the same way everywhere.
    Verified directly: both cosign v2.4.1 and v3.1.2 correctly verify this
    repo's actual signed images (produced by `sign_image.py` with
    `--new-bundle-format=false --use-signing-config=false
    --registry-referrers-mode=legacy`) using a bare `cosign verify --key ...`
    with no format flag, and both correctly fail (nonzero exit, "no signatures
    found") against an actually-unsigned image.
    """
    with registry_auth_dir(creds, image_ref) as auth_dir:
        command = ["cosign", "verify", "--key", key_path, image_ref]
        run_cmd(
            command,
            env={"DOCKER_CONFIG": auth_dir} if auth_dir else None,
            timeout=COSIGN_TIMEOUT,
        )


def sort_kernel_releases(kernel_releases: Sequence[str]) -> list[str]:
    """Return unique kernel release strings in stable natural-sort order."""
    return sorted(dict.fromkeys(kernel_releases), key=kernel_release_sort_key)


def extract_fedora_version(kernel_release: str) -> str:
    """
    Parse Fedora major version (for example `43`) from a kernel release.

    Example kernel release: `6.18.12-200.fc43.x86_64`.
    """
    match = FEDORA_FROM_KERNEL_RE.match(kernel_release)
    if not match:
        raise CiToolError(f"Failed to extract Fedora version from kernel release {kernel_release}")
    return match.group(1)

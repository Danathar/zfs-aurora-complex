"""
Script: files/scripts/configure_signing_policy.py
What: Writes trust policy for this repository's signed image path.
Doing: Adds one `policy.json` rule and one `registries.d` discovery file for
the final image repository.
Why: After the first boot into this image family, future `bootc upgrade`
operations should verify signatures from this repository automatically.
Goal: Keep the single-repository signing story boring and predictable.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

DEFAULT_POLICY_FILE = Path("/etc/containers/policy.json")
DEFAULT_REGISTRIES_DIR = Path("/etc/containers/registries.d")
DEFAULT_KEYS_DIR = Path("/etc/pki/containers")
REGISTRY_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def required_env(name: str) -> str:
    """Return one required environment variable or raise a clear error."""

    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def key_path_from_env(*, signing_key_filename: str) -> Path:
    """
    Resolve the public-key path used in the trust policy.

    Tests can override `KEY_PATH` directly. Normal image builds only need
    `SIGNING_KEY_FILENAME`.
    """

    override = os.environ.get("KEY_PATH", "").strip()
    if override:
        return Path(override)
    return DEFAULT_KEYS_DIR / signing_key_filename


def policy_file_from_env() -> Path:
    """Return the target `policy.json` path, allowing test overrides."""

    override = os.environ.get("POLICY_FILE", "").strip()
    return Path(override) if override else DEFAULT_POLICY_FILE


def registries_dir_from_env() -> Path:
    """Return the target `registries.d` directory, allowing test overrides."""

    override = os.environ.get("REGISTRIES_DIR", "").strip()
    return Path(override) if override else DEFAULT_REGISTRIES_DIR


def registry_file_path(*, image_repo: str, registries_dir: Path) -> Path:
    """Return the discovery-file path for one image repository."""

    filename = REGISTRY_FILENAME_SAFE_RE.sub("-", image_repo).strip("-")
    return registries_dir / f"{filename}.yaml"


def load_policy(policy_path: Path) -> dict[str, object]:
    """Load `policy.json`, or create the default root structure if absent."""

    if policy_path.exists():
        return json.loads(policy_path.read_text(encoding="utf-8"))
    return {"default": [{"type": "insecureAcceptAnything"}]}


def update_policy(*, policy_data: dict[str, object], image_repo: str, key_path: Path) -> dict[str, object]:
    """Insert or replace the repo-specific trust rule inside one policy document."""

    transports = policy_data.setdefault("transports", {})
    docker_transport = transports.setdefault("docker", {})
    docker_transport[image_repo] = [
        {
            "type": "sigstoreSigned",
            "keyPath": str(key_path),
            "signedIdentity": {"type": "matchRepository"},
        }
    ]
    return policy_data


def write_registry_discovery_file(*, image_repo: str, registry_file: Path) -> None:
    """Write the sigstore-discovery file for one image repository."""

    registry_file.write_text(
        "# Sigstore attachment discovery for the published image repository.\n"
        "docker:\n"
        f"  {image_repo}:\n"
        "    use-sigstore-attachments: true\n",
        encoding="utf-8",
    )


def main() -> None:
    image_repo = required_env("IMAGE_REPO")
    signing_key_filename = required_env("SIGNING_KEY_FILENAME")

    policy_path = policy_file_from_env()
    registries_dir = registries_dir_from_env()
    key_path = key_path_from_env(signing_key_filename=signing_key_filename)
    registry_file = registry_file_path(image_repo=image_repo, registries_dir=registries_dir)

    key_path.parent.mkdir(parents=True, exist_ok=True)
    registries_dir.mkdir(parents=True, exist_ok=True)
    policy_path.parent.mkdir(parents=True, exist_ok=True)

    policy_data = load_policy(policy_path)
    updated_policy = update_policy(
        policy_data=policy_data,
        image_repo=image_repo,
        key_path=key_path,
    )
    policy_path.write_text(json.dumps(updated_policy, indent=2) + "\n", encoding="utf-8")
    write_registry_discovery_file(image_repo=image_repo, registry_file=registry_file)

    os.chmod(policy_path, 0o644)
    os.chmod(registry_file, 0o644)
    if key_path.exists():
        os.chmod(key_path, 0o644)


if __name__ == "__main__":
    main()

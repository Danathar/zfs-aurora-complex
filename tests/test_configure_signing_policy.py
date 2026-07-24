"""
Script: tests/test_configure_signing_policy.py
What: Tests for the in-image signing-policy helper.
Doing: Loads the helper from its tracked script path, writes policy/discovery files into a temporary directory, and verifies the resulting content.
Why: The native image build now calls a pure Python helper instead of a shell-plus-inline-Python script.
Goal: Keep repository trust policy generation readable, deterministic, and testable.
"""

from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "files"
    / "scripts"
    / "configure_signing_policy.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location("configure_signing_policy", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ConfigureSigningPolicyTests(unittest.TestCase):
    def test_main_writes_policy_and_registry_discovery_files(self) -> None:
        module = load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            policy_file = temp_root / "policy.json"
            registries_dir = temp_root / "registries.d"
            key_path = temp_root / "keys" / "zfs-aurora-complex.pub"
            key_path.parent.mkdir(parents=True, exist_ok=True)
            key_path.write_text("public-key", encoding="utf-8")

            with patch.dict(
                os.environ,
                {
                    "IMAGE_REPO": "ghcr.io/example/zfs-aurora-complex",
                    "SIGNING_KEY_FILENAME": "zfs-aurora-complex.pub",
                    "POLICY_FILE": str(policy_file),
                    "REGISTRIES_DIR": str(registries_dir),
                    "KEY_PATH": str(key_path),
                },
                clear=False,
            ):
                module.main()

            policy_data = json.loads(policy_file.read_text(encoding="utf-8"))
            self.assertEqual(
                policy_data["transports"]["docker"]["ghcr.io/example/zfs-aurora-complex"][0]["keyPath"],
                str(key_path),
            )

            registry_file = registries_dir / "ghcr.io-example-zfs-aurora-complex.yaml"
            registry_text = registry_file.read_text(encoding="utf-8")
            self.assertIn("ghcr.io/example/zfs-aurora-complex", registry_text)
            self.assertIn("use-sigstore-attachments: true", registry_text)

    def test_registry_file_path_uses_full_repo_path(self) -> None:
        module = load_module()
        registries_dir = Path("/tmp/registries.d")

        first = module.registry_file_path(
            image_repo="ghcr.io/danathar/zfs-aurora-complex",
            registries_dir=registries_dir,
        )
        second = module.registry_file_path(
            image_repo="ghcr.io/other/zfs-aurora-complex",
            registries_dir=registries_dir,
        )

        self.assertEqual(first.name, "ghcr.io-danathar-zfs-aurora-complex.yaml")
        self.assertEqual(second.name, "ghcr.io-other-zfs-aurora-complex.yaml")
        self.assertNotEqual(first, second)

    def test_registry_file_path_preserves_dots_and_hyphens(self) -> None:
        module = load_module()
        registry_file = module.registry_file_path(
            image_repo="registry.example.io/org-name/zfs.aurora-complex",
            registries_dir=Path("/tmp/registries.d"),
        )

        self.assertEqual(
            registry_file.name,
            "registry.example.io-org-name-zfs.aurora-complex.yaml",
        )


if __name__ == "__main__":
    unittest.main()

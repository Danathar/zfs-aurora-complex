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
from pathlib import Path
import tempfile
import unittest
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
            key_path = temp_root / "keys" / "zfs-aurora-containerfile.pub"
            key_path.parent.mkdir(parents=True, exist_ok=True)
            key_path.write_text("public-key", encoding="utf-8")

            with patch.dict(
                os.environ,
                {
                    "IMAGE_REPO": "ghcr.io/example/zfs-aurora-containerfile",
                    "SIGNING_KEY_FILENAME": "zfs-aurora-containerfile.pub",
                    "POLICY_FILE": str(policy_file),
                    "REGISTRIES_DIR": str(registries_dir),
                    "KEY_PATH": str(key_path),
                },
                clear=False,
            ):
                module.main()

            policy_data = json.loads(policy_file.read_text(encoding="utf-8"))
            self.assertEqual(
                policy_data["transports"]["docker"]["ghcr.io/example/zfs-aurora-containerfile"][0]["keyPath"],
                str(key_path),
            )

            registry_file = registries_dir / "zfs-aurora-containerfile.yaml"
            registry_text = registry_file.read_text(encoding="utf-8")
            self.assertIn("ghcr.io/example/zfs-aurora-containerfile", registry_text)
            self.assertIn("use-sigstore-attachments: true", registry_text)


if __name__ == "__main__":
    unittest.main()

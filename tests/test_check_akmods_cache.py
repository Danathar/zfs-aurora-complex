"""
Script: tests/test_check_akmods_cache.py
What: Tests for shared akmods cache validation helpers.
Doing: Creates temporary RPM trees and checks missing-kernel detection.
Why: Protects the multi-kernel cache check added for base images with fallback kernels.
Goal: Keep rebuild decisions fail-closed when any required kernel RPM is absent.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from ci_tools.check_akmods_cache import _missing_kernel_releases


class CheckAkmodsCacheTests(unittest.TestCase):
    def test_reports_missing_kernel_releases(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            rpm_dir = root / "rpms" / "kmods" / "zfs"
            rpm_dir.mkdir(parents=True, exist_ok=True)
            (rpm_dir / "kmod-zfs-6.18.13-200.fc43.x86_64-2.4.1-1.fc43.x86_64.rpm").touch()

            missing = _missing_kernel_releases(
                root,
                [
                    "6.18.13-200.fc43.x86_64",
                    "6.18.16-200.fc43.x86_64",
                ],
            )

            self.assertEqual(missing, ["6.18.16-200.fc43.x86_64"])


if __name__ == "__main__":
    unittest.main()

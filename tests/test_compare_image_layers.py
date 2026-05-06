"""
Script: tests/test_compare_image_layers.py
What: Tests the chunkah image comparison helper.
Doing: Exercises manifest parsing, layer reuse accounting, and markdown output without invoking skopeo.
Why: Branch CI should report image-size differences with deterministic calculations.
Goal: Keep chunkah comparison behavior reviewable before the GitHub workflow runs.
"""

from __future__ import annotations

import json
import unittest

from ci_tools.compare_image_layers import build_report, markdown_report, summarize_manifest


def _manifest(layers: list[tuple[str, int]]) -> str:
    return json.dumps(
        {
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {"mediaType": "application/vnd.oci.image.config.v1+json"},
            "layers": [
                {
                    "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
                    "digest": digest,
                    "size": size,
                }
                for digest, size in layers
            ],
        }
    )


class CompareImageLayersTests(unittest.TestCase):
    def test_build_report_counts_shared_layers_and_sizes(self) -> None:
        main = summarize_manifest(
            "main",
            "docker://example/main:latest",
            "sha256:main",
            _manifest([("sha256:a", 10), ("sha256:b", 20)]),
        )
        branch = summarize_manifest(
            "branch",
            "docker://example/main:test",
            "sha256:branch",
            _manifest([("sha256:a", 10), ("sha256:c", 5)]),
        )

        report = build_report(main, branch)

        self.assertEqual(report["main"]["total_layer_size"], 30)
        self.assertEqual(report["branch"]["total_layer_size"], 15)
        self.assertEqual(report["comparison"]["shared_layer_count"], 1)
        self.assertEqual(report["comparison"]["branch_unique_layer_count"], 1)
        self.assertEqual(report["comparison"]["branch_unique_layer_size"], 5)
        self.assertEqual(report["comparison"]["total_layer_size_delta"], -15)
        self.assertTrue(report["comparison"]["media_types_match"])

    def test_markdown_report_includes_pull_estimate(self) -> None:
        main = summarize_manifest(
            "main",
            "docker://example/main:latest",
            "sha256:main",
            _manifest([("sha256:a", 1024)]),
        )
        branch = summarize_manifest(
            "branch",
            "docker://example/main:test",
            "sha256:branch",
            _manifest([("sha256:b", 2048)]),
        )

        output = markdown_report(build_report(main, branch))

        self.assertIn("Estimated bytes to pull moving main -> branch", output)
        self.assertIn("2.0 KiB", output)


if __name__ == "__main__":
    unittest.main()

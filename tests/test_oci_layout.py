"""
Script: tests/test_oci_layout.py
What: Tests the shared OCI-layout reader that both the cache check and the image build use.
Doing: Exercises layer discovery on a manifest with no usable layers, and drives unpack_layer_tarballs against real tarballs so the successful extraction -- not only its rejections -- is asserted.
Why: The rejection branches were already covered through tests/test_install_zfs_from_akmods_cache.py, but nothing ever let a benign layer through, so the extract call itself and its `filter="data"` hardening were unexercised.
Goal: Pin the behaviour that unpacks registry-supplied layers into the build root.

`shared/oci_layout.py` is reached by ci_tools/check_akmods_cache.py and by
containerfiles/zfs-akmods/install_zfs_from_akmods_cache.py, and the bytes it
unpacks come from a container registry. Both callers patch it out in their own
tests, so it is tested here directly.
"""

from __future__ import annotations

import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from shared.oci_layout import load_layer_files_from_oci_layout, unpack_layer_tarballs


def _write_manifest(layout_dir: Path, layers: list[dict[str, str]]) -> None:
    (layout_dir / "manifest.json").write_text(
        json.dumps({"layers": layers}), encoding="utf-8"
    )


def _write_layer(path: Path, entries: list[tuple[str, bytes]], *, mode: int = 0o644) -> None:
    """Write a tarball containing one regular file per (name, payload) entry."""

    with tarfile.open(path, "w") as tar_handle:
        for name, payload in entries:
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mode = mode
            tar_handle.addfile(info, io.BytesIO(payload))


class LoadLayerFilesFromOciLayoutTests(unittest.TestCase):
    def test_manifest_with_no_layers_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            layout_dir = Path(temp_dir)
            _write_manifest(layout_dir, [])

            with self.assertRaisesRegex(RuntimeError, "No layers found in OCI layout"):
                load_layer_files_from_oci_layout(layout_dir)

    def test_manifest_whose_layers_all_lack_a_digest_is_an_error(self) -> None:
        # A digest-less entry is dropped by the comprehension, which can empty
        # the list even though the manifest did list layers. That still has to
        # fail loudly rather than return nothing to unpack.
        with tempfile.TemporaryDirectory() as temp_dir:
            layout_dir = Path(temp_dir)
            _write_manifest(layout_dir, [{"mediaType": "application/vnd.oci.image.layer.v1.tar"}])

            with self.assertRaisesRegex(RuntimeError, "No layers found in OCI layout"):
                load_layer_files_from_oci_layout(layout_dir)

    def test_digest_less_entries_are_skipped_and_the_rest_are_resolved(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            layout_dir = Path(temp_dir)
            _write_manifest(
                layout_dir,
                [
                    {"digest": ""},
                    {"mediaType": "application/vnd.oci.image.layer.v1.tar"},
                    {"digest": "sha256:aaaa"},
                ],
            )

            self.assertEqual(
                load_layer_files_from_oci_layout(layout_dir),
                [layout_dir / "aaaa"],
            )


class UnpackLayerTarballsTests(unittest.TestCase):
    def test_every_layer_is_extracted_into_the_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            destination = root / "extract"
            destination.mkdir()
            first = root / "first.tar"
            second = root / "second.tar"
            _write_layer(first, [("rpms/zfs-2.4.0-1.fc43.x86_64.rpm", b"first-layer")])
            _write_layer(
                second,
                [
                    ("rpms/zfs-dkms-2.4.0-1.fc43.noarch.rpm", b"second-layer"),
                    ("usr/lib/modules/6.18.12-200.fc43.x86_64/kernel.ko", b"module"),
                ],
            )

            unpack_layer_tarballs([first, second], destination)

            self.assertEqual(
                (destination / "rpms/zfs-2.4.0-1.fc43.x86_64.rpm").read_bytes(),
                b"first-layer",
            )
            self.assertEqual(
                (destination / "rpms/zfs-dkms-2.4.0-1.fc43.noarch.rpm").read_bytes(),
                b"second-layer",
            )
            self.assertEqual(
                (destination / "usr/lib/modules/6.18.12-200.fc43.x86_64/kernel.ko").read_bytes(),
                b"module",
            )

    def test_extraction_clears_setuid_bits_from_layer_members(self) -> None:
        # This is what `filter="data"` buys over an unfiltered extractall: a
        # layer cannot land a setuid binary in the build root. Without the
        # filter the extracted mode is 0o4755, so this assertion is what
        # notices if the argument is dropped.
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            destination = root / "extract"
            destination.mkdir()
            layer = root / "layer.tar"
            _write_layer(layer, [("usr/bin/backdoor", b"payload")], mode=0o4755)

            unpack_layer_tarballs([layer], destination)

            extracted = destination / "usr/bin/backdoor"
            self.assertEqual(extracted.stat().st_mode & 0o7777, 0o755)

    def test_absolute_member_paths_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            destination = root / "extract"
            destination.mkdir()
            layer = root / "layer.tar"
            _write_layer(layer, [("/etc/passwd", b"root:x:0:0")])

            with self.assertRaisesRegex(RuntimeError, "Unsafe tar path"):
                unpack_layer_tarballs([layer], destination)

    def test_an_unsafe_member_after_a_safe_one_still_rejects_the_whole_layer(self) -> None:
        # The scan runs over every member before anything is written, so a
        # layer that opens with legitimate files cannot smuggle an escaping
        # member past it, and nothing from that layer reaches the destination.
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            destination = root / "extract"
            destination.mkdir()
            layer = root / "layer.tar"
            _write_layer(
                layer,
                [
                    ("rpms/zfs-2.4.0-1.fc43.x86_64.rpm", b"legitimate"),
                    ("../../escape", b"escaped"),
                ],
            )

            with self.assertRaisesRegex(RuntimeError, "Unsafe tar path"):
                unpack_layer_tarballs([layer], destination)

            self.assertEqual(list(destination.iterdir()), [])

    def test_a_later_layer_is_not_unpacked_once_an_earlier_one_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            destination = root / "extract"
            destination.mkdir()
            bad = root / "bad.tar"
            good = root / "good.tar"
            _write_layer(bad, [("../escape", b"escaped")])
            _write_layer(good, [("rpms/zfs-2.4.0-1.fc43.x86_64.rpm", b"legitimate")])

            with self.assertRaisesRegex(RuntimeError, "Unsafe tar path"):
                unpack_layer_tarballs([bad, good], destination)

            self.assertEqual(list(destination.iterdir()), [])

    def test_an_empty_layer_list_extracts_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "extract"
            destination.mkdir()

            unpack_layer_tarballs([], destination)

            self.assertEqual(list(destination.iterdir()), [])


if __name__ == "__main__":
    unittest.main()

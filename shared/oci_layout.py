"""
Module: shared/oci_layout.py
What: Shared helpers for reading and unpacking local OCI layout directories.
Doing: Reads `manifest.json`, resolves layer tarball paths, and unpacks those
files after checking that archive members stay inside the target directory.
Why: Both CI cache inspection and the image build unpack OCI layers. Keeping
that logic in one module avoids drift between two implementations.
Goal: Make OCI-layer inspection easier to read and maintain.
"""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
import tarfile


def load_layer_files_from_oci_layout(layout_dir: Path) -> list[Path]:
    """Resolve manifest layer digests into local tarball paths."""

    manifest_path = layout_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    layer_files = [
        layout_dir / str(layer["digest"]).removeprefix("sha256:")
        for layer in manifest.get("layers", [])
        if layer.get("digest")
    ]
    if not layer_files:
        raise RuntimeError(f"No layers found in OCI layout {layout_dir}")
    return layer_files


def _is_safe_tar_member(name: str) -> bool:
    """
    Reject absolute or parent-directory entries before extraction.

    Why: these helpers unpack under a temporary working directory, so archive
    members should never escape that destination.
    """

    path = PurePosixPath(name)
    return not path.is_absolute() and ".." not in path.parts


def unpack_layer_tarballs(layer_files: list[Path], destination: Path) -> None:
    """Extract each OCI layer tarball after validating member paths."""

    for layer_path in layer_files:
        with tarfile.open(layer_path, "r") as layer_tar:
            for member in layer_tar.getmembers():
                if not _is_safe_tar_member(member.name):
                    raise RuntimeError(
                        f"Unsafe tar path found in layer {layer_path}: {member.name}"
                    )
            layer_tar.extractall(destination)

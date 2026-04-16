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


def _is_safe_tar_path(path_str: str) -> bool:
    """Return True when a tar member path stays under the destination."""

    path = PurePosixPath(path_str)
    return not path.is_absolute() and ".." not in path.parts


def _is_safe_tar_member(member: tarfile.TarInfo) -> bool:
    """
    Reject members that could escape the extraction destination.

    Why: these helpers unpack under a temporary working directory, so archive
    members should never escape it. An attacker-controlled layer could ship a
    symlink or hardlink whose `name` is safe but whose `linkname` points out of
    the destination, so the link target is validated too.

    Note: `extractall(..., filter='data')` below is the load-bearing security
    check (Python 3.12+ rejects the same unsafe cases and more). This explicit
    pre-scan exists to produce a clearer error message with the offending
    member name before the extractor runs.
    """

    if not _is_safe_tar_path(member.name):
        return False
    if member.islnk() or member.issym():
        if not _is_safe_tar_path(member.linkname):
            return False
    return True


def unpack_layer_tarballs(layer_files: list[Path], destination: Path) -> None:
    """Extract each OCI layer tarball after validating member paths."""

    for layer_path in layer_files:
        with tarfile.open(layer_path, "r") as layer_tar:
            for member in layer_tar.getmembers():
                if not _is_safe_tar_member(member):
                    raise RuntimeError(
                        f"Unsafe tar path found in layer {layer_path}: {member.name}"
                    )
            # `filter='data'` activates tarfile's built-in safe filter (Python
            # 3.12+), which rejects device files, setuid bits, and link targets
            # that escape the destination. It's a second layer of defense on
            # top of the explicit checks above.
            layer_tar.extractall(destination, filter="data")

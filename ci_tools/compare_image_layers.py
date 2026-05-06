"""
Script: ci_tools/compare_image_layers.py
What: Compares two published image manifests for layer-size and reuse differences.
Doing: Reads raw manifests with skopeo, summarizes media types, layer counts, compressed sizes, and shared layer digests.
Why: The chunkah experiment needs a repeatable CI-visible answer about whether rechunking improves this image.
Goal: Keep image comparison small, explicit, and easy to audit from workflow logs.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Callable, Mapping

from ci_tools.common import CiToolError, optional_env, require_env, run_cmd


@dataclass(frozen=True)
class Layer:
    digest: str
    size: int
    media_type: str


@dataclass(frozen=True)
class ImageSummary:
    name: str
    ref: str
    digest: str
    manifest_media_type: str
    config_media_type: str
    layers: list[Layer]

    @property
    def total_layer_size(self) -> int:
        return sum(layer.size for layer in self.layers)


def _run_skopeo_raw(image_ref: str) -> str:
    return run_cmd(["skopeo", "inspect", "--raw", image_ref])


def _run_skopeo_digest(image_ref: str) -> str:
    output = run_cmd(["skopeo", "inspect", "--format", "{{ .Digest }}", image_ref])
    return output.strip()


def summarize_manifest(name: str, image_ref: str, digest: str, raw_manifest: str) -> ImageSummary:
    try:
        manifest = json.loads(raw_manifest)
    except json.JSONDecodeError as exc:
        raise CiToolError(f"Could not parse raw manifest JSON for {image_ref}") from exc

    layers_data = manifest.get("layers")
    if not isinstance(layers_data, list):
        media_type = str(manifest.get("mediaType") or "unknown")
        raise CiToolError(
            f"Expected a single-image manifest with layers for {image_ref}, got {media_type}"
        )

    config = manifest.get("config")
    config_media_type = ""
    if isinstance(config, dict):
        config_media_type = str(config.get("mediaType") or "")

    layers: list[Layer] = []
    for layer in layers_data:
        if not isinstance(layer, dict):
            raise CiToolError(f"Unexpected layer entry in manifest for {image_ref}")
        digest_value = str(layer.get("digest") or "")
        size_value = layer.get("size")
        media_type = str(layer.get("mediaType") or "")
        if not digest_value or not isinstance(size_value, int):
            raise CiToolError(f"Layer in {image_ref} is missing digest or integer size")
        layers.append(Layer(digest=digest_value, size=size_value, media_type=media_type))

    return ImageSummary(
        name=name,
        ref=image_ref,
        digest=digest,
        manifest_media_type=str(manifest.get("mediaType") or ""),
        config_media_type=config_media_type,
        layers=layers,
    )


def load_image_summary(
    name: str,
    image_ref: str,
    *,
    raw_loader: Callable[[str], str] = _run_skopeo_raw,
    digest_loader: Callable[[str], str] = _run_skopeo_digest,
) -> ImageSummary:
    return summarize_manifest(
        name,
        image_ref,
        digest_loader(image_ref),
        raw_loader(image_ref),
    )


def _layer_digest_set(summary: ImageSummary) -> set[str]:
    return {layer.digest for layer in summary.layers}


def _unique_size(summary: ImageSummary, other: ImageSummary) -> int:
    other_digests = _layer_digest_set(other)
    return sum(layer.size for layer in summary.layers if layer.digest not in other_digests)


def _summary_dict(summary: ImageSummary) -> dict:
    return {
        "name": summary.name,
        "ref": summary.ref,
        "digest": summary.digest,
        "manifest_media_type": summary.manifest_media_type,
        "config_media_type": summary.config_media_type,
        "layer_count": len(summary.layers),
        "total_layer_size": summary.total_layer_size,
        "layers": [
            {
                "digest": layer.digest,
                "size": layer.size,
                "media_type": layer.media_type,
            }
            for layer in summary.layers
        ],
    }


def build_report(main: ImageSummary, branch: ImageSummary) -> dict:
    main_digests = _layer_digest_set(main)
    branch_digests = _layer_digest_set(branch)
    shared_digests = main_digests & branch_digests
    branch_unique_size = _unique_size(branch, main)
    main_unique_size = _unique_size(main, branch)

    return {
        "main": _summary_dict(main),
        "branch": _summary_dict(branch),
        "comparison": {
            "shared_layer_count": len(shared_digests),
            "branch_unique_layer_count": len(branch_digests - main_digests),
            "main_unique_layer_count": len(main_digests - branch_digests),
            "branch_unique_layer_size": branch_unique_size,
            "main_unique_layer_size": main_unique_size,
            "total_layer_size_delta": branch.total_layer_size - main.total_layer_size,
            "layer_count_delta": len(branch.layers) - len(main.layers),
            "media_types_match": (
                branch.manifest_media_type == main.manifest_media_type
                and branch.config_media_type == main.config_media_type
            ),
        },
    }


def _format_bytes(value: int) -> str:
    sign = "-" if value < 0 else ""
    absolute = float(abs(value))
    for unit in ("B", "KiB", "MiB", "GiB"):
        if absolute < 1024 or unit == "GiB":
            if unit == "B":
                return f"{sign}{int(absolute)} {unit}"
            return f"{sign}{absolute:.1f} {unit}"
        absolute /= 1024
    raise AssertionError("unreachable")


def markdown_report(report: Mapping) -> str:
    main = report["main"]
    branch = report["branch"]
    comparison = report["comparison"]

    size_delta = int(comparison["total_layer_size_delta"])
    branch_unique = int(comparison["branch_unique_layer_size"])
    layer_delta = int(comparison["layer_count_delta"])
    media_match = bool(comparison["media_types_match"])
    conclusion = "No clear size advantage from chunkah in this run."
    if size_delta < 0:
        conclusion = "chunkah reduced total compressed layer size in this run."

    return "\n".join(
        [
            "## chunkah image comparison",
            "",
            f"- Main image: `{main['ref']}`",
            f"- Main digest: `{main['digest']}`",
            f"- Branch image: `{branch['ref']}`",
            f"- Branch digest: `{branch['digest']}`",
            f"- Main media types: `{main['manifest_media_type']}` / `{main['config_media_type']}`",
            f"- Branch media types: `{branch['manifest_media_type']}` / `{branch['config_media_type']}`",
            f"- Media types match: `{str(media_match).lower()}`",
            f"- Main compressed layer size: `{_format_bytes(int(main['total_layer_size']))}`",
            f"- Branch compressed layer size: `{_format_bytes(int(branch['total_layer_size']))}`",
            f"- Size delta: `{_format_bytes(size_delta)}`",
            f"- Main layer count: `{main['layer_count']}`",
            f"- Branch layer count: `{branch['layer_count']}`",
            f"- Layer count delta: `{layer_delta}`",
            f"- Shared layer digests: `{comparison['shared_layer_count']}`",
            f"- Estimated bytes to pull moving main -> branch: `{_format_bytes(branch_unique)}`",
            "",
            f"Conclusion: {conclusion}",
        ]
    )


def write_report(report: dict, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def append_step_summary(markdown: str) -> None:
    summary_path = optional_env("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    with open(summary_path, "a", encoding="utf-8") as handle:
        handle.write(markdown)
        handle.write("\n")


def main() -> None:
    main_ref = require_env("MAIN_IMAGE_REF")
    branch_ref = require_env("BRANCH_IMAGE_REF")
    output_path = Path(optional_env("REPORT_JSON_PATH", "artifacts/chunkah-image-comparison.json"))

    report = build_report(
        load_image_summary("main", main_ref),
        load_image_summary("branch", branch_ref),
    )
    write_report(report, output_path)
    markdown = markdown_report(report)
    print(markdown)
    append_step_summary(markdown)


if __name__ == "__main__":
    main()

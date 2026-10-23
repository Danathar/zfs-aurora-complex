"""
Script: ci_tools/check_akmods_cache.py
What: Checks whether the shared akmods cache can be reused for the current primary base-image kernel.
Doing: Pins and pulls the cache image, checks for a matching `kmod-zfs` RPM and a valid
cosign signature, then writes cache state outputs.
Why: Skip rebuild when safe, but rebuild when the required primary-kernel module set is
missing, older than the current target kernel, or not signed by this repo's own key.
Goal: Control rebuild decisions in main and validation workflows.
"""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ci_tools.common import (
    REPO_ROOT,
    CiToolError,
    cosign_verify,
    normalize_owner,
    optional_env,
    registry_creds_from_env,
    require_env,
    skopeo_copy,
    skopeo_inspect_json_optional,
    write_github_outputs,
)
from ci_tools.pin_akmods_cache import akmods_cache_image_tag
from shared.oci_layout import load_layer_files_from_oci_layout, unpack_layer_tarballs


@dataclass(frozen=True)
class AkmodsCacheStatus:
    """
    Result of checking one shared akmods cache image against the required kernel.

    `image_exists` tells us whether the source tag is present at all.
    `source_image_pinned` is the exact image digest that was inspected.
    `missing_release` is the fail-closed kernel not covered by that image at
    the required exact ZFS version, and `required_zfs_version` records which
    version that was so a rebuild reason can say why the cache was rejected.
    `signature_verified` is only meaningful once `missing_release` is empty:
    there is no point checking a signature on an image that does not even
    have the right kmod-zfs RPM. A reusable cache must satisfy all three.
    """

    source_image: str
    image_exists: bool
    source_image_pinned: str = ""
    missing_release: str = ""
    required_zfs_version: str = ""
    signature_verified: bool = False
    inspection_method: str = "unpacked-image"

    @property
    def content_matches(self) -> bool:
        """
        True when the cache holds the right kmod-zfs, regardless of signature.

        Kept separate from `reusable` because the two questions are asked at
        different points. Reuse asks "may I trust this cache someone else
        built?", which requires a signature. The post-rebuild verification asks
        "did the build produce the ZFS version I resolved?", which cannot
        require one: that check runs inside the akmods job, while signing
        happens in a later, separate job, so a freshly rebuilt cache is always
        still unsigned at that moment.
        """

        return self.image_exists and not self.missing_release

    @property
    def reusable(self) -> bool:
        """True only when the cache exists, covers the required kernel, and is signed."""

        return self.content_matches and self.signature_verified


def _has_kernel_matching_rpm(root_dir: Path, kernel_release: str, zfs_version: str) -> bool:
    # We only trust cache reuse when an RPM exists for this exact kernel string
    # *and* the exact ZFS patch version this run resolved. If the cache only has
    # RPMs for older kernels, that cache is out of date. Matching only the minor
    # line (e.g. "2.4.*") used to let a cached 2.4.3 build satisfy a resolved
    # 2.4.4 world, so a new OpenZFS patch -- including a security fix -- would
    # never reach the image as long as the kernel and line stayed the same.
    #
    # Cached payloads are named
    # `kmod-zfs-<kernel_release>-<zfs_version>-<rel>.<dist>.<arch>.rpm`, for
    # example `kmod-zfs-7.1.4-204.fc44.x86_64-2.4.3-1.fc44.x86_64.rpm`. Matching
    # the full `<major>.<minor>.<patch>` string followed by a hyphen means a
    # `2.4.3` cache never satisfies a `2.4.30` requirement or vice versa.
    rpm_dir = root_dir / "rpms" / "kmods" / "zfs"
    if not rpm_dir.exists():
        return False
    pattern = f"kmod-zfs-{kernel_release}-{zfs_version}-*.rpm"
    return any(rpm_dir.glob(pattern))


def _require_cache_digest_ref(pinned_image: str, *, image_org: str, source_repo: str) -> str:
    """
    Return `pinned_image` if it is a digest ref into this run's cache repository.

    A caller that hands in a pinned ref is saying "check exactly this image".
    Anything else -- a tag, a different repository, a truncated digest -- would
    make the check vouch for an image other than the one the caller consumes,
    so it is refused rather than resolved.
    """

    expected = re.compile(
        rf"ghcr\.io/{re.escape(image_org)}/{re.escape(source_repo)}@sha256:[0-9a-f]{{64}}"
    )
    if not expected.fullmatch(pinned_image):
        raise CiToolError(
            f"Refusing to check akmods cache ref {pinned_image!r}: expected "
            f"ghcr.io/{image_org}/{source_repo}@sha256:<64 hex digits>, the digest ref "
            "pin-akmods-cache publishes for this repository."
        )
    return pinned_image


def inspect_akmods_cache(
    *,
    image_org: str,
    source_repo: str,
    fedora_version: str,
    kernel_release: str,
    zfs_version: str,
    verify_signature: bool = True,
    creds: str | None = None,
    pinned_image: str = "",
) -> AkmodsCacheStatus:
    """
    Inspect one shared akmods cache image and report whether it is reusable.

    This helper is shared by the main workflow and the read-only validation
    workflows so they all make the same cache-reuse decision.

    `creds` (a `username:password` pair) authenticates every registry call made
    here. It is not optional in practice for a repository whose GHCR cache
    package is private, which is what a fresh fork gets by default: GHCR answers
    an *anonymous* request for a private-or-nonexistent package with `denied` /
    `unauthorized` rather than `manifest unknown`, deliberately refusing to
    confirm whether the package exists. `skopeo_inspect_json_optional` correctly
    declines to read that as "missing" and re-raises, which aborts the job --
    including the rebuild step that would have created the cache in the first
    place. So the very first probe on a fresh fork deadlocks the bootstrap:
    the check cannot say "rebuild" because it cannot get far enough to look.

    An established deployment hides this. Its cache package is already public,
    so the anonymous probe succeeds and nothing here ever needed a token.

    By default the shared tag is resolved to a digest here, and that digest is
    both the one checked and the one reported as `source_image_pinned`.
    `pinned_image` skips the tag lookup and checks a digest the caller already
    pinned. The post-rebuild verification needs that: the run signs and builds
    from the digest `pin-akmods-cache` published, and a second, independent
    read of the mutable tag could land on a different image.
    """

    source_image = akmods_cache_image_tag(
        image_org=image_org, source_repo=source_repo, fedora_version=fedora_version
    )
    if pinned_image:
        source_image_pinned = _require_cache_digest_ref(
            pinned_image, image_org=image_org, source_repo=source_repo
        )
    else:
        inspect_json = skopeo_inspect_json_optional(f"docker://{source_image}", creds=creds)
        if inspect_json is None:
            return AkmodsCacheStatus(
                source_image=source_image,
                image_exists=False,
                missing_release=kernel_release,
                required_zfs_version=zfs_version,
                inspection_method="missing-image",
            )

        source_digest = str(inspect_json.get("Digest") or "")
        if not source_digest:
            raise CiToolError(
                f"Missing digest in skopeo inspect output for docker://{source_image}"
            )

        source_image_pinned = f"ghcr.io/{image_org}/{source_repo}@{source_digest}"
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        akmods_dir = root / "akmods"
        skopeo_copy(f"docker://{source_image_pinned}", f"dir:{akmods_dir}", creds=creds)

        try:
            layer_files = load_layer_files_from_oci_layout(akmods_dir)
            unpack_layer_tarballs(layer_files, root)
        except RuntimeError as exc:
            raise CiToolError(str(exc)) from exc

        has_match = _has_kernel_matching_rpm(root, kernel_release, zfs_version)
        if not has_match:
            return AkmodsCacheStatus(
                source_image=source_image,
                image_exists=True,
                source_image_pinned=source_image_pinned,
                missing_release=kernel_release,
                required_zfs_version=zfs_version,
                inspection_method="unpacked-image",
            )

    # Only verify the signature once the RPM content has already been
    # confirmed correct -- no point checking who signed a cache that does not
    # even have the RPM this run needs. This cache is a real supply-chain
    # input (any run holding a registry write token can republish the shared tag),
    # so a reused cache must be signed by this repo's own key, not just
    # contain a filename that happens to match.
    #
    # Callers verifying a cache this same run just rebuilt pass
    # verify_signature=False: signing runs in a later job, so the image is
    # legitimately unsigned at that point and a cosign call would be a
    # guaranteed failure against an image nobody has signed yet.
    #
    # The credentials matter here for a second reason: fetching a signature is
    # an ordinary registry read, so against a private cache package an
    # anonymous `cosign verify` fails on auth, not on the signature. Because
    # any failure is caught below and read as "not signed", that would quietly
    # reject a perfectly good, correctly signed cache on every single run --
    # fail-closed, but it would rebuild the kernel modules forever and never
    # say why.
    signature_verified = False
    if verify_signature:
        signature_verified = True
        try:
            cosign_verify(
                source_image_pinned,
                key_path=str(REPO_ROOT / "cosign.pub"),
                creds=creds,
            )
        except CiToolError:
            signature_verified = False

    return AkmodsCacheStatus(
        source_image=source_image,
        image_exists=True,
        source_image_pinned=source_image_pinned,
        missing_release="",
        required_zfs_version=zfs_version,
        signature_verified=signature_verified,
        inspection_method="unpacked-image",
    )


def main() -> None:
    image_org = normalize_owner(require_env("GITHUB_REPOSITORY_OWNER"))
    fedora_version = require_env("FEDORA_VERSION")
    kernel_release = require_env("KERNEL_RELEASE")
    source_repo = require_env("AKMODS_REPO")
    zfs_version = require_env("ZFS_VERSION")
    # Required, not optional, and deliberately so: falling back to anonymous
    # access when the workflow forgets to pass a token is how this went
    # unnoticed. On a public cache package anonymous calls just work, so the
    # omission stays invisible until someone forks the repo and hits a
    # `denied` on their first cache probe. Failing here names the real problem
    # instead. Same pattern as check_stable_signal.py and promote_stable.py.
    creds = registry_creds_from_env(required=True)
    # Strict mode is used after a rebuild, where "no reusable cache" is not a
    # normal answer but a failure: it means the cache this run just published
    # does not contain the ZFS version this run resolved and is about to label
    # the image with. See the "Verify the rebuilt cache" step in
    # .github/actions/prepare-main-akmods/action.yml.
    require_match = optional_env("REQUIRE_MATCH").lower() == "true"
    # Strict mode checks the exact digest the "Resolve shared akmods cache
    # digest" step published, because that digest is what this run signs and
    # builds the candidate from. Re-reading the shared tag here would check
    # whatever it points at by now, so a missing pin is an error, not a cue to
    # fall back to the tag. Read with optional_env because only strict mode
    # needs it; the reuse check that runs first has no pin yet.
    pinned_image = optional_env("AKMODS_IMAGE_PINNED") if require_match else ""
    if require_match and not pinned_image:
        raise CiToolError(
            "REQUIRE_MATCH=true needs AKMODS_IMAGE_PINNED, the digest ref pin-akmods-cache "
            "published. Refusing to verify the shared tag instead: it can point at a different "
            "image than the one this run signs and builds from."
        )

    status = inspect_akmods_cache(
        image_org=image_org,
        source_repo=source_repo,
        fedora_version=fedora_version,
        kernel_release=kernel_release,
        zfs_version=zfs_version,
        verify_signature=not require_match,
        creds=creds,
        pinned_image=pinned_image,
    )

    if require_match:
        # Strict mode answers a different question from the reuse decision
        # below, so it returns here rather than sharing that output path. It
        # deliberately skipped the signature check (signing runs in a later
        # job), which leaves `reusable` false -- falling through would print
        # "signature could not be verified ... akmods rebuild is required"
        # immediately after a successful rebuild and write exists=false, both
        # of which are actively misleading to anyone reading the log.
        if not status.content_matches:
            raise CiToolError(
                f"Shared akmods cache {status.source_image} does not provide a kmod-zfs for "
                f"primary kernel {kernel_release} at ZFS version {zfs_version} even after a "
                "rebuild. The akmods build resolves its own OpenZFS version independently of "
                "this repo, so the two can disagree. Refusing to continue: the image would be "
                f"labelled org.zfs-aurora-complex.zfs-version={zfs_version} while actually "
                "shipping whatever the cache really contains."
            )
        print(
            f"Verified the rebuilt {status.source_image} ({status.source_image_pinned}) "
            f"contains kmod-zfs for primary kernel {kernel_release} at ZFS version "
            f"{zfs_version}, matching the version this image will be labelled with. "
            "Signature is not checked here; this cache is signed by a later job."
        )
        return

    if not status.image_exists:
        write_github_outputs({"exists": "false"})
        print(f"No existing shared akmods cache image for Fedora {fedora_version}; rebuild is required.")
        return

    if status.reusable:
        write_github_outputs(
            {
                "exists": "true",
                "akmods_image": status.source_image,
                "akmods_image_pinned": status.source_image_pinned,
            }
        )
        print(
            f"Found matching, signed {status.source_image} kmods for primary kernel "
            f"{kernel_release} at ZFS version {zfs_version}; "
            f"akmods rebuild can be skipped. Inspection method: {status.inspection_method}."
        )
        print(f"Checked akmods cache digest: {status.source_image_pinned}")
        return

    write_github_outputs({"exists": "false"})
    if status.missing_release:
        print(
            f"Cached {status.source_image} is present but has no kmod-zfs for primary kernel "
            f"{status.missing_release} at ZFS version {zfs_version}; "
            "akmods rebuild is required."
        )
    else:
        print(
            f"Cached {status.source_image} ({status.source_image_pinned}) has a matching "
            f"kmod-zfs for primary kernel {kernel_release} at ZFS version {zfs_version}, "
            "but its cosign signature could not be verified against cosign.pub; "
            "akmods rebuild is required. This is expected the first time this check runs "
            "after signing was added, or if a cache was published before this repo's key "
            "was configured for akmods signing."
        )


if __name__ == "__main__":
    main()

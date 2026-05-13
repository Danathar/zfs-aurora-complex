# Native container build for the Aurora DX NVIDIA + ZFS image.
#
# This repository intentionally avoids BlueBuild. The build is expressed as a
# standard bootc-style Containerfile so CI can control tags directly.

ARG BASE_IMAGE="ghcr.io/ublue-os/aurora-dx-nvidia:latest"
# Aurora already includes the Universal Blue brew payload. If BASE_IMAGE is
# changed to a base that does not include brew, such as Fedora Atomic, uncomment
# BREW_IMAGE, the brew stage, and the COPY --from=brew line below.
# ARG BREW_IMAGE="ghcr.io/ublue-os/brew:latest"

FROM scratch AS ctx
COPY build_files /
COPY containerfiles /containerfiles
COPY files /files
COPY shared /shared
COPY cosign.pub /cosign.pub

# FROM ${BREW_IMAGE} AS brew

FROM ${BASE_IMAGE}

# These build arguments are supplied by CI for each run.
#
# Local builds should not bake in one Fedora major version here. When CI does
# not pass an explicit akmods image reference, the helper can render this
# template with the Fedora version detected from the chosen base image.
ARG AKMODS_IMAGE=""
ARG AKMODS_IMAGE_TEMPLATE="ghcr.io/danathar/zfs-aurora-complex-akmods:main-{fedora}"
ARG IMAGE_REPO="ghcr.io/danathar/zfs-aurora-complex"
ARG SIGNING_KEY_FILENAME="zfs-aurora-complex.pub"

# Convert the build arguments into environment variables once so the helper
# script can read stable names while the Containerfile stays declarative.
ENV AKMODS_IMAGE="${AKMODS_IMAGE}"
ENV AKMODS_IMAGE_TEMPLATE="${AKMODS_IMAGE_TEMPLATE}"
ENV IMAGE_REPO="${IMAGE_REPO}"
ENV SIGNING_KEY_FILENAME="${SIGNING_KEY_FILENAME}"

# Optional brew payload import for bases that do not already include brew.
# COPY --from=brew /system_files /

# Keep the custom build logic in repo files rather than a long inline RUN.
COPY --from=ctx / /

RUN --mount=type=cache,target=/var/cache \
    --mount=type=cache,target=/var/log \
    --mount=type=tmpfs,target=/tmp \
    /build-image.sh

RUN bootc container lint

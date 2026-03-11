# Native container build for the Kinoite + ZFS image.
#
# This repository intentionally avoids BlueBuild. The build is expressed as a
# standard bootc-style Containerfile so CI can control tags directly.

ARG BASE_IMAGE="ghcr.io/ublue-os/kinoite-main:latest"
ARG BREW_IMAGE="ghcr.io/ublue-os/brew:latest"

FROM scratch AS ctx
COPY build_files /
COPY containerfiles /containerfiles
COPY files /files
COPY cosign.pub /cosign.pub

FROM ${BREW_IMAGE} AS brew

FROM ${BASE_IMAGE}

# These build arguments are supplied by CI for each run.
ARG AKMODS_IMAGE="ghcr.io/danathar/zfs-kinoite-containerfile-akmods:main-43"
ARG IMAGE_REPO="ghcr.io/danathar/zfs-kinoite-containerfile"
ARG SIGNING_KEY_FILENAME="zfs-kinoite-containerfile.pub"

# Convert the build arguments into environment variables once so the helper
# script can read stable names while the Containerfile stays declarative.
ENV AKMODS_IMAGE="${AKMODS_IMAGE}"
ENV IMAGE_REPO="${IMAGE_REPO}"
ENV SIGNING_KEY_FILENAME="${SIGNING_KEY_FILENAME}"

# The brew OCI image already packages the tarball, systemd presets, shell
# integration, and first-boot setup units.
COPY --from=brew /system_files /

# Keep the custom build logic in repo files rather than a long inline RUN.
COPY --from=ctx / /

RUN --mount=type=cache,target=/var/cache \
    --mount=type=cache,target=/var/log \
    --mount=type=tmpfs,target=/tmp \
    /build-image.sh

RUN bootc container lint

# Native container build for the Aurora + ZFS image.
#
# This repository intentionally avoids BlueBuild. The build is expressed as a
# standard bootc-style Containerfile so CI can control tags directly.

ARG BASE_IMAGE="ghcr.io/ublue-os/aurora-dx:latest"
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
#
# ARG values declared in this stage are already visible as shell environment
# variables to the RUN instruction below, so build-image.sh can read them
# directly without a separate ENV block.
ARG AKMODS_IMAGE=""
ARG AKMODS_IMAGE_TEMPLATE="ghcr.io/danathar/zfs-aurora-complex-akmods:main-{fedora}"
ARG IMAGE_REPO="ghcr.io/danathar/zfs-aurora-complex"
ARG SIGNING_KEY_FILENAME="zfs-aurora-complex.pub"

# Optional brew payload import for bases that do not already include brew.
# COPY --from=brew /system_files /

# Bind-mount the build context instead of COPYing it so none of these files
# (build-image.sh, containerfiles/, files/, shared/, cosign.pub) end up baked
# into the published image's filesystem.
RUN --mount=type=bind,from=ctx,source=/,target=/ctx \
    --mount=type=cache,target=/var/cache \
    --mount=type=cache,target=/var/log \
    --mount=type=tmpfs,target=/tmp \
    /ctx/build-image.sh

RUN bootc container lint

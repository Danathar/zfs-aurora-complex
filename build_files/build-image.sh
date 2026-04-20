#!/usr/bin/env bash
#
# Script: build_files/build-image.sh
# What: Applies all image customizations in one place during the native build.
# Doing: Enables brew services, installs cached ZFS RPMs, writes the in-image
#        signing policy, and commits the ostree container.
# Why: A separate build script is easier to read than one large Containerfile
#      shell block, and it keeps the teaching comments close to the steps.
# Goal: Produce one bootable Aurora image with ZFS, upstream Aurora defaults,
#       brew, and repository trust configuration.
#
set -euo pipefail

# Build-time configuration is passed from the Containerfile as environment
# variables so the script can stay reusable in GitHub Actions workflow runs and local tests.
: "${IMAGE_REPO:?Missing IMAGE_REPO}"
: "${SIGNING_KEY_FILENAME:?Missing SIGNING_KEY_FILENAME}"

# `install_zfs_from_akmods_cache.py` accepts either:
# 1. `AKMODS_IMAGE` for an exact override, or
# 2. `AKMODS_IMAGE_TEMPLATE` for "follow the Fedora version in this base image".
# CI passes the exact image today, while local builds usually rely on the
# template path so they do not need a hard-coded Fedora release number here.

# Copy the committed public key into the standard trust-material directory.
install -d -m 0755 /etc/pki/containers /etc/containers/registries.d
install -m 0644 /cosign.pub "/etc/pki/containers/${SIGNING_KEY_FILENAME}"

# The OCI brew image ships systemd units and preset files. Presetting them at
# build time means first boot automatically performs the brew extraction step.
/usr/bin/systemctl preset brew-setup.service
/usr/bin/systemctl preset brew-update.timer
/usr/bin/systemctl preset brew-upgrade.timer

# Distrobox is already included by upstream Aurora. If this image needs to add
# Fedora RPM packages during the container build, prefer `dnf5 -y install ...`.
# `rpm-ostree install distrobox`

# Install ZFS userspace + module payloads from the self-hosted akmods cache.
python3 /containerfiles/zfs-akmods/install_zfs_from_akmods_cache.py

# Write repository-specific trust policy into the final image so future signed
# updates from the same GitHub Container Registry (GHCR) path work without extra
# host-side repair steps.
IMAGE_REPO="${IMAGE_REPO}" \
SIGNING_KEY_FILENAME="${SIGNING_KEY_FILENAME}" \
python3 /files/scripts/configure_signing_policy.py

# `bootc container lint` expects package-created state directories under `/var`
# to have matching tmpfiles declarations. The `zfs` dependency chain pulls in
# `pcp`, which creates `/var/lib/pcp/*` directories but does not ship tmpfiles
# entries for this image build mode, so install a local declaration here.
install -D -m 0644 \
  /files/usr/lib/tmpfiles.d/zfs-aurora-complex.conf \
  /usr/lib/tmpfiles.d/zfs-aurora-complex.conf

# Remove build-only runtime state before the final ostree commit.
# Why these paths are safe to drop:
# 1. `/run` is runtime-only state and should not be baked into the image.
# 2. `/var/lib/containers` here came from build-time image inspection, not from
#    something users need at runtime after deployment.
# 3. Some builders leave resolver files bind-mounted under `/run/systemd`.
#    Those specific paths can be busy, so cleanup here must be best-effort
#    instead of failing the entire image build on a harmless leftover mount.
# The `|| true` guards below are intentional:
# - `/run/systemd/resolve` may be an active bind mount that cannot be unmounted
#   during this build step, so the umount attempt is best-effort.
# - `2>/dev/null` on the find commands suppresses errors when a directory has
#   already been removed by an earlier `-exec` in the same invocation.
mountpoint -q /run/systemd/resolve && umount /run/systemd/resolve || true
find /run/systemd -mindepth 1 \
  ! -path '/run/systemd/resolve' \
  ! -path '/run/systemd/resolve/*' \
  -exec rm -rf {} + 2>/dev/null || true
find /run/systemd -depth -type d -empty -delete 2>/dev/null || true
rm -rf /var/lib/containers

# `ostree container commit` finalizes package-layering changes into the image.
ostree container commit

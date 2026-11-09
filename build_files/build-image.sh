#!/usr/bin/env bash
#
# Script: build_files/build-image.sh
# What: Applies all image customizations in one place during the native build.
# Doing: Enables brew services, keeps brew's files out of root's login shells,
#        installs cached ZFS RPMs, writes the in-image signing policy, and
#        cleans up build-only state.
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
install -m 0644 /ctx/cosign.pub "/etc/pki/containers/${SIGNING_KEY_FILENAME}"

# The OCI brew image ships systemd units and preset files. Presetting them at
# build time means first boot automatically performs the brew extraction step.
/usr/bin/systemctl preset brew-setup.service
/usr/bin/systemctl preset brew-update.timer
/usr/bin/systemctl preset brew-upgrade.timer

# The aurora-dx base image already carries the `ublue-os/brew` payload, and with
# it three login-shell fragments that execute code out of
# /home/linuxbrew/.linuxbrew:
#
#   /etc/profile.d/brew.sh                  evals `brew shellenv`
#   /etc/profile.d/brew-bash-completion.sh  runs `brew completions link`, then
#                                           sources every file in the prefix's
#                                           etc/bash_completion.d
#   /usr/share/fish/vendor_conf.d/ublue-brew.fish
#                                           runs `brew shellenv fish` and sources it
#
# `brew-setup.service`, preset above, ends with `chown -R 1000:1000
# /home/linuxbrew`, so that prefix is owned by the desktop user on every booted
# machine. /etc/profile.d and the fish vendor directory are read by every login
# shell, root's included (`su -`, `sudo -i`, a console or SSH root login), so
# sourcing them as root runs user-writable code as root with no password.
#
# Remove them and put the prefix on PATH instead, for its owner only and
# without executing anything from it. This departs from upstream Aurora's
# defaults on purpose: brew's login-time environment and bash completions are
# no longer loaded.
rm -f \
  /etc/profile.d/brew.sh \
  /etc/profile.d/brew-bash-completion.sh \
  /usr/share/fish/vendor_conf.d/ublue-brew.fish

install -D -m 0644 \
  /ctx/files/etc/profile.d/brew-path.sh \
  /etc/profile.d/brew-path.sh

# Fail closed if a future base image ships another one. Which files the base
# image carries is decided upstream and changes whenever BASE_IMAGE moves, so no
# static check in this tree can see it -- only a build-time sweep of what
# actually landed can. Anything left that mentions brew in a login-shell
# directory must be the fragment installed just above.
check_brew_login_fragments() {
  local root="${1:-}"
  local found
  found="$(
    grep -rlI -- brew \
      "${root}/etc/profile.d" \
      "${root}/etc/fish/conf.d" \
      "${root}/usr/share/fish/vendor_conf.d" 2>/dev/null \
      | grep -vxF -e "${root}/etc/profile.d/brew-path.sh" || true
  )"
  if [ -n "${found}" ]; then
    echo "Unreviewed brew login-shell fragment(s) from the base image:" >&2
    echo "${found}" >&2
    echo "Review each one, then either remove it above or add it to the allow-list." >&2
    return 1
  fi
}

check_brew_login_fragments

# The same payload's `brew-setup.service`, preset above, stages its tarball
# through the fixed path /tmp/homebrew as root. /tmp is a world-writable tmpfs on a
# booted system and `mkdir -p` exits 0 on an existing symlink instead of replacing it,
# so an account that creates /tmp/homebrew first has root extract through its symlink
# and has its own extra files copied into the prefix the next ExecStart hands to UID
# 1000. Give the unit a private /tmp instead of restating upstream's ExecStart= chain,
# so a future base image cannot drift away from a copy of it.
install -D -m 0644 \
  /ctx/files/usr/lib/systemd/system/brew-setup.service.d/10-private-tmp.conf \
  /usr/lib/systemd/system/brew-setup.service.d/10-private-tmp.conf

# `PrivateTmp=` only contains staging that happens under /tmp or /var/tmp. If a future
# base image stages somewhere else, the drop-in above becomes decoration and
# nothing in this tree would say so, because the unit is not ours to read at any
# revision -- only the build can see the one that actually landed.
check_brew_setup_staging() {
  local unit="${1:-}/usr/lib/systemd/system/brew-setup.service"
  if [ ! -f "${unit}" ]; then
    echo "brew-setup.service missing from the base image: ${unit}" >&2
    return 1
  fi
  if ! grep -E '^ExecStart=' "${unit}" | grep -qE '(^|[= ])/(var/)?tmp(/|[[:space:]]|$)'; then
    echo "brew-setup.service no longer stages under /tmp or /var/tmp:" >&2
    grep -E '^ExecStart=' "${unit}" >&2
    echo "PrivateTmp= in 10-private-tmp.conf cannot contain that. Re-read the unit," >&2
    echo "then either widen the drop-in or drop it." >&2
    return 1
  fi
}

check_brew_setup_staging

# Distrobox is already included by upstream Aurora. If this image needs to add
# Fedora RPM packages during the container build, prefer `dnf5 -y install ...`.
# `rpm-ostree install distrobox`
# dnf5 -y install <package>

# crun-krun provides /usr/bin/krun, the OCI runtime that lets Podman run a
# container inside a libkrun microVM (`podman run --runtime krun ...`). It sits
# beside the default `crun` runtime and does not replace it.
# `crun-krun` Requires the exact `crun` version-release, so an unversioned
# install would upgrade the base image's `crun` whenever Fedora updates is ahead
# of Aurora. Pin it to the `crun` the base already ships; the base's Updates
# Archive repo keeps that build available. `rpm -q` fails (and `set -e` stops the
# build) if `crun` is missing, and `dnf5` fails if the pinned build is gone, so
# the build fails instead of drifting.
crun_evr="$(rpm -q --qf '%{EPOCHNUM}:%{VERSION}-%{RELEASE}' crun)"
dnf5 -y install "crun-krun-${crun_evr}"

# Install ZFS userspace + module payloads from the self-hosted akmods cache.
python3 /ctx/containerfiles/zfs-akmods/install_zfs_from_akmods_cache.py

# Load the ZFS kernel module at boot so the installed userspace tools can report
# both userspace and kernel-module versions without requiring a manual modprobe.
install -D -m 0644 \
  /ctx/files/usr/lib/modules-load.d/zfs.conf \
  /usr/lib/modules-load.d/zfs.conf

# Write repository-specific trust policy into the final image so future signed
# updates from the same GitHub Container Registry (GHCR) path work without extra
# host-side repair steps.
IMAGE_REPO="${IMAGE_REPO}" \
SIGNING_KEY_FILENAME="${SIGNING_KEY_FILENAME}" \
python3 /ctx/files/scripts/configure_signing_policy.py

# `bootc container lint` expects package-created state directories under `/var`
# to have matching tmpfiles declarations. The `zfs` dependency chain pulls in
# `pcp`, which creates `/var/lib/pcp/*` directories but does not ship tmpfiles
# entries for this image build mode, so install a local declaration here.
install -D -m 0644 \
  /ctx/files/usr/lib/tmpfiles.d/zfs-aurora-complex.conf \
  /usr/lib/tmpfiles.d/zfs-aurora-complex.conf

# Remove build-only runtime state before `bootc container lint` runs.
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

# Drop the cache and lock state `dnf5` leaves behind after the ZFS install
# above. Both currently show up as `bootc container lint` warnings:
# - `/run/dnf` trips `nonempty-run-tmp`. `/run` is a tmpfs on a booted system,
#   so anything baked in here is masked at boot and is pure image weight.
# - `/var/lib/dnf` trips `var-tmpfiles`. Content baked into `/var` is only
#   applied at initial install and is never refreshed by a later
#   `bootc upgrade`, so build-time cache has no business being there: every
#   machine installed from the image would carry this run's stale copy forever.
# Neither path holds anything the booted system needs. Installed-package state
# lives in the rpm database under `/usr`, and dnf's versionlock configuration
# lives in `/etc/dnf`; dnf recreates its own cache directories on demand.
rm -rf /run/dnf /var/lib/dnf

# No explicit `ostree container commit` here: `bootc container lint` (run next,
# in the Containerfile) already performs the equivalent validation/finalization.
# Universal Blue dropped the explicit commit step from their own templates for
# the same reason (ublue-os/image-template#137, ublue-os/aurora#1216).

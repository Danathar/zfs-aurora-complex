# Install, Rebase, And Verify

If a term is unfamiliar, check the shared glossary first:
[`docs/glossary.md`](./glossary.md)

## Purpose

Operator-facing steps: switching a machine onto this image, checking that
ZFS actually works afterwards, and verifying the image signature by hand.

## Install And Rebase

> [!WARNING]
> This is a single-maintainer image stream. It is production for its author —
> daily-driven on real hardware with real ZFS pools — but that means the bar it
> has cleared is "safe enough for one person's own machines," not a vendor
> support commitment to anyone else. The pipeline builds, signs, and promotes
> automatically (see "Safety Model" above), but nothing in it currently boots
> the image or imports a pool before `:latest` moves. Switching a machine you
> depend on onto this image means trusting that bar, not a guarantee.

Fresh stock Aurora DX can switch to the published image after the GitHub workflow
has produced a signed `latest` tag:

```bash
sudo bootc switch --enforce-container-sigpolicy ghcr.io/danathar/zfs-aurora-complex:latest
sudo systemctl reboot
```

That `--enforce-container-sigpolicy` flag is intentional. It makes the first
custom-image deployment use the in-image container signature policy instead of
recording the origin as an unverified registry image.

If a test VM was already switched with plain `bootc switch`, switch it again
with the command above and reboot before relying on `bootc upgrade`.

Why this image flow stays easier to reason about:

1. the stable and candidate image tags live in the same repository
2. after you boot into this image family once, the in-image policy only needs to trust one repository path
3. there is no dual-repository policy normalization or host repair path to keep in sync

## Quick Validation After Boot

```bash
rpm -q kmod-zfs
modinfo zfs | head
lsmod | grep '^zfs'
zpool --version
zfs --version
distrobox --version
brew --version
```

For virtual machine (VM) testing with a secondary disk:

```bash
sudo wipefs -a /dev/vdb
sudo zpool create -f -o ashift=12 -O mountpoint=none testpool /dev/vdb
sudo zfs create -o mountpoint=/var/mnt/testpool testpool/data
sudo zpool status
sudo zfs list
```

## Signature Verification

```bash
cosign verify \
  --key cosign.pub \
  --new-bundle-format=false \
  ghcr.io/danathar/zfs-aurora-complex:latest
```

`--new-bundle-format=false` is required: this repo signs with legacy cosign
registry attachments so Fedora/Aurora's bootc signature policy path can
discover them via `use-sigstore-attachments`, which default cosign v3
verification does not use. For the full signing model, key rotation, and the
in-image trust policy, read [`docs/signing-and-bootc.md`](./signing-and-bootc.md).

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
has produced a signed `latest` tag.

### Step 1: Trust The Signing Key On The Host

The image installs `cosign.pub`, a `policy.json` rule, and a `registries.d`
discovery file for this repository (see
[`docs/signing-and-bootc.md`](./signing-and-bootc.md)), but those files only
exist on a machine that has already booted this image family. On the first
switch the host has none of them, so the host is what has to carry them. Do this
once per host, before switching:

```bash
# From a clone of this repository, so `cosign.pub` is the committed one.
sudo install -Dm0644 cosign.pub /etc/pki/containers/zfs-aurora-complex.pub

sudo install -d -m 0755 /etc/containers/registries.d
sudo tee /etc/containers/registries.d/ghcr.io-danathar-zfs-aurora-complex.yaml >/dev/null <<'YAML'
docker:
  ghcr.io/danathar/zfs-aurora-complex:
    use-sigstore-attachments: true
YAML
```

The `use-sigstore-attachments` entry is not optional here: this repository signs
with legacy cosign registry attachments so the bootc policy path can discover
them, and without that entry the policy engine does not look for the signature
at all. The April 2026 incident note in
[`docs/signing-and-bootc.md`](./signing-and-bootc.md) is the long version.

Then add this to the `transports.docker` map in `/etc/containers/policy.json`.
Edit the existing file rather than replacing it, and create the `transports` or
`docker` level if the stock file lacks it:

```json
"ghcr.io/danathar/zfs-aurora-complex": [
  {
    "type": "sigstoreSigned",
    "keyPath": "/etc/pki/containers/zfs-aurora-complex.pub",
    "signedIdentity": { "type": "matchRepository" }
  }
]
```

These are the same three artifacts the image itself writes at build time, so
this step bootstraps the steady state rather than introducing a second
mechanism. For a fork, use your own repository path, your own `cosign.pub`, and
a key filename matching that fork's `SIGNING_KEY_FILENAME`.

### Step 2: Switch

```bash
sudo bootc switch --enforce-container-sigpolicy ghcr.io/danathar/zfs-aurora-complex:latest
sudo systemctl reboot
```

That `--enforce-container-sigpolicy` flag is intentional, and it does two
things. It makes bootc evaluate this pull against the container signature
policy, which on the first switch is the host's `/etc/containers/policy.json` --
the file step 1 just taught about this repository. And it records the origin as
policy-verified rather than as an unverified registry image, so every later
`bootc upgrade` enforces the same rule, then reading it from inside the booted
image.

Skipping step 1 does not make the switch fail. Stock Aurora DX's policy
defaults to `insecureAcceptAnything`, so the pull that installs the entire
operating system is accepted with no signature check, and enforcement starts
only afterwards -- against whatever that unverified pull installed. The manual
`cosign verify` under [Signature Verification](#signature-verification) below
checks the published image, but it is a separate command an operator has to
choose to run, not part of the switch.

If a test VM was already switched with plain `bootc switch`, complete step 1,
switch again with the command above, and reboot before relying on
`bootc upgrade`.

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

## Testing A Change Before It Reaches Latest

No branch push publishes an image. A pull request builds the image with
`build-pr.yml`, but that run publishes and signs nothing.

To test a change on a machine, use one of two paths:

1. **Local image.** Build it as described in
   [`docs/building-locally.md`](./building-locally.md).
2. **Signed candidate.** After the change is merged to `main`, run the main
   workflow with `workflow_dispatch` on `main` and `promote_to_stable=false`.
   That publishes a fully signed `candidate-*` tag without moving `latest`. It
   is switchable with enforcement, exactly like production.

A dispatch on any other ref builds nothing.

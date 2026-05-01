# Signing And Bootc Trust

This document describes how this repository signs published bootc images, how
the image enforces that trust during future upgrades, and which compatibility
details matter for current Fedora/Aurora bootc systems.

## Short Version

This repository uses a self-managed cosign keypair:

1. `cosign.pub` is committed to the repo.
2. The matching private key is stored in the GitHub Actions secret
   `SIGNING_SECRET`.
3. CI signs the exact image digest after pushing the candidate image.
4. The image build installs `cosign.pub` into `/etc/pki/containers/`.
5. The image writes `/etc/containers/policy.json` and a
   `/etc/containers/registries.d/` file so future `bootc upgrade` operations
   require a matching signature for `ghcr.io/danathar/zfs-aurora-complex`.

Use this command for the first switch from stock Aurora DX into this image:

```bash
sudo bootc switch --enforce-container-sigpolicy ghcr.io/danathar/zfs-aurora-complex:latest
sudo systemctl reboot
```

After booting into this image family, normal upgrades should be:

```bash
sudo bootc upgrade
```

## What Gets Signed

Container tags move. Digests do not.

For that reason, the signing helper resolves the pushed tag to an immutable
digest and signs:

```text
ghcr.io/danathar/zfs-aurora-complex@sha256:<digest>
```

It does not sign just `:latest` as a string. The tag is only a convenient way to
find the digest. The signature is tied to the content digest.

This is why promotion does not need to sign `latest` a second time. Promotion
copies the already-signed candidate digest to `latest` and to an audit tag. If
all three tags resolve to the same digest, the existing digest signature covers
all of them.

## Publication Order

The main workflow avoids leaving user-facing tags on unsigned content.

For the candidate image, `.github/actions/publish-native-image/action.yml` does
this:

1. builds the local image with the requested candidate tag
2. retags it to a transient non-publishable tag:
   `candidate-<sha>-<fedora>-unsigned-<run_id>`
3. pushes only the transient tag first
4. resolves the transient tag to a digest
5. signs that digest
6. verifies that digest signature
7. copies the same digest to the requested candidate tag

Then the promote job copies the same digest to:

1. `latest`
2. `stable-<run>-<sha>`

If the sign step fails, the user-facing candidate tag and `latest` do not move.

## In-Image Trust Policy

During the image build, `build_files/build-image.sh` installs the public key and
calls `files/scripts/configure_signing_policy.py`.

That helper writes a repository-specific policy for:

```text
ghcr.io/danathar/zfs-aurora-complex
```

The policy type is `sigstoreSigned`, with:

```json
{
  "type": "sigstoreSigned",
  "keyPath": "/etc/pki/containers/zfs-aurora-complex.pub",
  "signedIdentity": {"type": "matchRepository"}
}
```

The image also writes a registries.d file for the same repository with:

```yaml
docker:
  ghcr.io/danathar/zfs-aurora-complex:
    use-sigstore-attachments: true
```

That `use-sigstore-attachments` entry is important. The containers/image policy
engine used by `bootc`, `skopeo`, `podman`, and related tooling needs it to
discover cosign signatures stored in a registry for `sigstoreSigned` policy
checks.

## Cosign V3 Compatibility

This repository intentionally uses legacy cosign signature attachments for now:

```bash
cosign sign \
  --yes \
  --new-bundle-format=false \
  --use-signing-config=false \
  --registry-referrers-mode=legacy \
  --key env://COSIGN_PRIVATE_KEY \
  ghcr.io/danathar/zfs-aurora-complex@sha256:<digest>
```

The matching verify command also uses:

```bash
cosign verify --new-bundle-format=false --key cosign.pub ...
```

Reason: cosign v3 defaults to newer Sigstore bundle and OCI referrer behavior.
That can be valid for `cosign verify`, but current bootc/containers-image
`sigstoreSigned` policy checks in this repo expect the older sigstore attachment
storage path enabled by `use-sigstore-attachments`.

The visible registry artifact for a bootc-compatible signature is a tag like:

```text
sha256-<digest-without-colon>.sig
```

If this tag is missing, `cosign verify` might still succeed with its default
new-format behavior while `bootc upgrade` fails with:

```text
A signature was required, but no signature exists
```

For this repo, the bootc-compatible verification check is:

```bash
cosign verify \
  --key cosign.pub \
  --new-bundle-format=false \
  ghcr.io/danathar/zfs-aurora-complex:latest
```

## Incident Note: What Went Wrong In April 2026

The failure was not caused by the VM, the first switch command, or a mismatched
public key. The VM had the correct signed origin, the committed `cosign.pub`
matched the public key installed under `/etc/pki/containers/`, and the in-image
policy correctly required signatures for this repository.

The break was a signature storage-format mismatch:

1. CI used cosign v3 defaults.
2. Cosign v3 wrote a newer Sigstore bundle / OCI-referrer signature.
3. Default `cosign verify` could find that signature.
4. The bootc/containers-image policy path used by this image looked for legacy
   sigstore attachments through `use-sigstore-attachments`.
5. The legacy `sha256-<digest>.sig` attachment did not exist, so `bootc upgrade`
   correctly reported that no usable signature existed.

The fix was to make `ci_tools/sign_image.py` sign and verify in the legacy
format that bootc policy can discover:

```bash
--new-bundle-format=false
--use-signing-config=false
--registry-referrers-mode=legacy
```

The first two flags select the older signature payload path and disable cosign
v3's default signing config flow. The third flag stores the signature as a
legacy registry attachment instead of only as an OCI referrer.

## Operational Checks

Inspect the current published digest:

```bash
skopeo inspect --format '{{ .Digest }}' \
  docker://ghcr.io/danathar/zfs-aurora-complex:latest
```

Verify the signature in the same format bootc policy expects:

```bash
cosign verify \
  --key cosign.pub \
  --new-bundle-format=false \
  ghcr.io/danathar/zfs-aurora-complex:latest
```

Check for the legacy signature attachment tag:

```bash
digest="$(skopeo inspect --format '{{ .Digest }}' \
  docker://ghcr.io/danathar/zfs-aurora-complex:latest)"
sig_tag="sha256-${digest#sha256:}.sig"
skopeo inspect "docker://ghcr.io/danathar/zfs-aurora-complex:${sig_tag}"
```

On a booted machine, confirm the origin is signed:

```bash
bootc status
```

The image reference should show `ostree-image-signed:docker://...` during
upgrade checks.

## Why `UpdateVersion` And `UpdateDigest` Appear

`UpdateVersion` and `UpdateDigest` in `bootc status` are bootc/ostree update
metadata. They are not signature records and they are not created by this repo's
build scripts.

In the common case after upgrading this repo's image, the status output can look
like this:

```text
Booted image: ghcr.io/danathar/zfs-aurora-complex:latest
  Digest: sha256:<current>

Rollback image: ghcr.io/danathar/zfs-aurora-complex:latest
  Digest: sha256:<previous>
  UpdateVersion: ...
  UpdateDigest: sha256:<current>
```

Those update fields are attached to the rollback deployment. They mean that, if
the rollback deployment were booted and checked against its tracked ref,
`ghcr.io/danathar/zfs-aurora-complex:latest`, the available update would be the
current digest. That is expected when both the booted and rollback deployments
track the same moving tag and `latest` now points at the image you are already
running.

This is why `bootc upgrade` can still correctly say:

```text
No update available.
```

The booted deployment already matches the current remote digest.

This is normal bootc/ostree behavior and can be seen on stock Universal Blue
images too. The important confirmation is that the fields are informational:
they describe what bootc last learned about the tracked image ref, not a pending
unsigned update and not a custom signing artifact.

## Key Rotation

Rotating the keypair changes which private key signs future images and which
public key booted machines trust.

Before publishing images signed by a new private key, make sure machines that
already booted this image have the new public key installed at:

```text
/etc/pki/containers/zfs-aurora-complex.pub
```

Manual install command:

```bash
sudo install -m 0644 cosign.pub /etc/pki/containers/zfs-aurora-complex.pub
```

If the registry image is signed with a new key but the machine still has the old
public key, `bootc upgrade` should fail. That is expected and is exactly what
the trust policy is supposed to enforce.

## Common Gotchas

1. `cosign verify` without `--new-bundle-format=false` can verify signatures
   that bootc's current policy path will not discover.
2. Signing a tag is not the same mental model as signing a digest. This repo
   signs digests.
3. Copying one signed digest to another tag does not require another signature.
4. Local builds are not automatically trusted by a strict bootc policy.
5. The policy is repository-specific. Renaming the image repository requires
   updating the image policy and registries.d scope.
6. Do not pass registry secrets in command argv. This repo authenticates with
   Docker login first, then lets cosign use the existing registry credentials.
7. Keep `cosign.key` out of git. Only `cosign.pub` belongs in the repository.

## References

- Bootc switch manual: https://bootc-dev.github.io/bootc/man/bootc-switch.html
- Sigstore container signing docs: https://docs.sigstore.dev/cosign/signing/signing_with_containers/
- containers-policy.json `sigstoreSigned` docs: https://manpages.debian.org/bookworm/golang-github-containers-image/containers-policy.json.5.en.html
- Cosign v3 release notes: https://github.com/sigstore/cosign/releases

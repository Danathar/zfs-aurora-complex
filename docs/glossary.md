# zfs-aurora-complex Glossary

This page defines terms used across this repository's docs and workflow comments.

## Core Terms

- `CI`: continuous integration. In this repo, that means the GitHub Actions workflow runs in `.github/workflows`.
- `CD`: continuous delivery or continuous deployment. In this repo, the publishing and promotion steps in the `main` workflow are the closest thing to CD.
- `candidate`: test tag built first on `main` before promotion moves `latest`.
- `stable`: the normal user-facing tag, `latest`.
- `audit tag`: immutable stable tag written during promotion so one published snapshot can be referenced later.
- `artifact`: a saved output file from a workflow run that you can inspect or reuse later.
- `manifest`: a structured data file that records what a run produced or which exact inputs it used.
- `checked-in defaults`: version-controlled default values stored in this repo, here in `ci/defaults.json`, instead of being copied into several workflow files.
- `fork`: a copy of another repository under a different GitHub account or organization. In this repo, `Danathar/akmods` is the fork used as the akmods source repository.
- `workflow`: one named GitHub Actions automation file.
- `workflow run`: one execution of a GitHub Actions workflow from start to finish.
- `pipeline`: the ordered set of jobs/steps in one workflow run.
- `composite action`: a local reusable GitHub Action made from several smaller steps. This repo uses them to keep the workflow files shorter without moving logic out of version control.
- `build context`: the set of local files available to the container build.
- `branch-scoped`: a tag/name that includes the branch identifier so branch artifacts stay isolated.
- `Fedora stream` / `kernel stream`: the ongoing flow of new kernel releases over time.
- `tag`: a human-readable image label like `latest` or `candidate-deadbee-43`.
- `image ref`: text that points to a container image, usually `name:tag` or `name@sha256:digest`.
- `digest`: an immutable hash that identifies one exact image content snapshot.
- `GHCR`: GitHub Container Registry, the image registry behind `ghcr.io`.
- `image owner portion`: the owner or organization part of an image path, for example `danathar` in `ghcr.io/danathar/zfs-aurora-complex`.
- `rebase` / `rebasing`: switching your installed OS image source to a different container image ref.
- `floating ref`: a tag-based ref such as `:latest` that can point to different content later.
- `pinned commit`: one exact Git commit SHA recorded on purpose so a build uses that exact source version and not whatever a branch tip points to later.
- `SHA`: short name for the long hash-like identifier Git uses for a commit object. In this repo, "pinned commit SHA" just means "the exact commit ID we want to build from."
- `digest-pinned ref`: an exact image pointer like `name@sha256:...`; it does not move unless you change the digest.
- `temporary checkout`: a short-lived local clone created only for the current CI run. In this repo, the akmods fork is cloned into `/tmp/akmods` and thrown away after the job ends.
- `signature`: cryptographic proof that an image digest was signed by a trusted key.
- `sigstore attachment`: the OCI artifact where tools like cosign store image signatures.
- `stop instead of guessing`: if a required safety input is missing, stop with an error instead of guessing.
- `out-of-date module` / `out-of-date kmod`: a kernel module built for an older kernel release than the one currently in the base image.
- `hardening`: add safety checks or stricter rules so failures are less likely and easier to catch early.
- `PR`: pull request.
- `automation account`: an automated account that triggered the workflow, for example `dependabot[bot]`.
- `VM`: virtual machine.
- `OCI`: Open Container Initiative standards used for container image formats and registries.
- `OCI layout`: a local on-disk directory format for container images. In this repo, cache checks copy an image into that format before unpacking its filesystem layers for inspection.
- `RPM`: Red Hat Package Manager package format. Fedora packages and kernel-module packages in this repo are all RPM files.
- `akmods`: Fedora-style tooling that builds kernel-module RPMs for a specific kernel release. In this repo, the "shared akmods cache image" is the container image that stores those prebuilt ZFS kernel-module RPMs.
- `YAML`: human-readable config format used by GitHub Actions workflows.
- `CLI`: command-line interface.

## Command Glossary

- `gh`: GitHub CLI.
- `skopeo`: reads/copies container images without running them.
- `podman`: builds and runs OCI containers locally.
- `buildah`: lower-level OCI image build tooling used by the GitHub Action in this repo.
- `rpm-ostree`: manages package layering/rebase on atomic Fedora systems like Aurora.
- `bootc`: tooling for building and switching bootable OCI images.
- `bootc container lint`: validates that a bootc-style container image meets the structural requirements expected by the bootc update system.
- `cosign`: signs and verifies container images.
- `depmod`: regenerates kernel module dependency metadata so the kernel can find newly installed modules at boot time. Used after installing ZFS kernel modules.
- `just`: task runner used by the upstream akmods repository.
- `ostree container commit`: finalizes package-layering changes inside a container build into the ostree commit format that bootc-based systems expect. This is the last step in `build-image.sh`.
- `systemctl preset`: applies vendor-supplied preset rules to enable or disable systemd units according to policy files shipped in the image. Used to activate brew services at build time.
- `tmpfiles.d`: systemd's mechanism for declaratively creating, deleting, or cleaning up files and directories at boot or on demand. This repo ships a `tmpfiles.d` entry for `pcp` state directories that ZFS dependencies pull in.
- `yq`: YAML processor used to update the upstream akmods target file.

## Configuration And Environment Variables

### Akmods Inputs

- `AKMODS_IMAGE`: exact shared akmods cache image ref used by the final image build to install ZFS RPMs.
- `AKMODS_IMAGE_TEMPLATE`: fallback cache-image template containing `{fedora}` for local builds that do not pass `AKMODS_IMAGE`.
- `AKMODS_REPO`: GHCR repository name for the shared akmods cache image.
- `AKMODS_UPSTREAM_REPO`: Git URL for the akmods fork this repo builds from.
- `AKMODS_UPSTREAM_REF`: exact akmods source commit or ref override used for a run.
- `AKMODS_UPSTREAM_TRACK`: floating akmods branch or tag resolved to a concrete SHA when no explicit upstream ref is pinned.
- `DEFAULT_AKMODS_REF`: workflow-dispatch override that can force one akmods source ref for a run.
- `ZFS_MINOR_VERSION`: OpenZFS minor line passed into the akmods build.
- `AKMODS_KERNEL`: kernel flavor value passed to upstream akmods tooling; this repo uses `main`.
- `AKMODS_TARGET`: akmods target name; this repo uses `zfs`.
- `AKMODS_VERSION`: Fedora major version value passed to upstream akmods tooling.
- `AKMODS_DESCRIPTION`: human-readable description written into the akmods publish target config.

### Image Build Inputs

- `BASE_IMAGE`: base Aurora image ref passed to the root `Containerfile`.
- `BREW_IMAGE`: Homebrew OCI image ref copied into the final image.
- `IMAGE_REPO`: final OS image repository path used when writing signing policy.
- `SIGNING_KEY_FILENAME`: public-key filename installed into the image for future signature verification.

### Resolved Run Inputs

- `FEDORA_VERSION`: Fedora major version resolved from the selected base image.
- `KERNEL_RELEASE`: newest detected kernel release, treated as the supported primary kernel.
- `DETECTED_KERNEL_RELEASES`: space-separated list of every kernel release found in the base image.
- `BASE_IMAGE_REF`: base image ref before digest pinning.
- `BASE_IMAGE_NAME`: base image repository name without the selected tag.
- `BASE_IMAGE_TAG`: tag selected from the base image stream for this run.
- `BASE_IMAGE_PINNED`: digest-pinned base image ref used for the build.
- `BASE_IMAGE_DIGEST`: digest portion of the selected base image.
- `BUILD_CONTAINER_REF`: build-container image ref before digest pinning.
- `BUILD_CONTAINER_PINNED`: digest-pinned build-container ref used by the akmods job.
- `BUILD_CONTAINER_DIGEST`: digest portion of the selected build-container image.

### Signing And Registry

- `SIGNING_SECRET`: repository secret containing the cosign private key.
- `COSIGN_PRIVATE_KEY`: environment variable used by the signing helper to pass the private key to cosign.
- `REGISTRY_ACTOR`: GitHub actor used as the registry username.
- `REGISTRY_TOKEN`: token used for GHCR authentication in helper commands.
- `REGISTRY_USER`: workflow-local registry username used by the Docker login step.
- `REGISTRY_PASSWORD`: workflow-local registry password or token used by the Docker login step.
- `IMAGE_ORG`: normalized image-owner portion used in GHCR paths.
- `IMAGE_NAME`: final OS image repository name.
- `IMAGE_TAG`: image tag being pushed, signed, or promoted.
- `HAS_SIGNING_SECRET`: workflow-local boolean that records whether `SIGNING_SECRET` is configured.

### Workflow Control And Audit

- `USE_INPUT_LOCK`: whether input-lock replay mode is enabled.
- `LOCK_FILE`: input-lock path passed into the resolver.
- `LOCK_FILE_PATH`: input-lock path recorded in the build-inputs manifest.
- `BRANCH_TAG_PREFIX`: branch-safe tag prefix used by branch builds.
- `AKMODS_FAILURE_LOG`: path to the captured akmods build log used for failure classification.
- `AKMODS_FAILURE_PAYLOAD_PATH`: path where the failure classifier writes the sticky-issue payload.

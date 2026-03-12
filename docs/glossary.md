# zfs-kinoite-containerfile Glossary

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
- `fork`: your copy of another repository under your own GitHub account. In this repo, `Danathar/akmods` is a fork used as the akmods source repository.
- `workflow`: one named GitHub Actions automation file.
- `workflow run`: one execution of a GitHub Actions workflow from start to finish.
- `pipeline`: the ordered set of jobs/steps in one workflow run.
- `composite action`: a local reusable GitHub Action made from several smaller steps. This repo uses them to keep the workflow files shorter without moving logic out of version control.
- `build context`: the set of local files available to the container build.
- `branch-scoped`: a tag/name that includes the branch identifier so branch artifacts stay isolated.
- `metadata tag`: a small companion image tag published next to a main image tag. In this repo, `main-<fedora>-metadata` stores the kernel list for the shared akmods cache.
- `Fedora stream` / `kernel stream`: the ongoing flow of new kernel releases over time.
- `tag`: a human-readable image label like `latest` or `candidate-deadbee-43`.
- `image ref`: text that points to a container image, usually `name:tag` or `name@sha256:digest`.
- `digest`: an immutable hash that identifies one exact image content snapshot.
- `GHCR`: GitHub Container Registry, the image registry behind `ghcr.io`.
- `image owner portion`: the owner or organization part of an image path, for example `danathar` in `ghcr.io/danathar/zfs-kinoite-containerfile`.
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
- `RPM`: Red Hat Package Manager package format. Fedora packages and kernel-module packages in this repo are all RPM files.
- `akmods`: Fedora-style tooling that builds kernel-module RPMs for a specific kernel release. In this repo, the "shared akmods cache image" is the container image that stores those prebuilt ZFS kernel-module RPMs.
- `YAML`: human-readable config format used by GitHub Actions workflows.
- `CLI`: command-line interface.

## Command Glossary

- `gh`: GitHub CLI.
- `skopeo`: reads/copies container images without running them.
- `podman`: builds and runs OCI containers locally.
- `buildah`: lower-level OCI image build tooling used by the GitHub Action in this repo.
- `rpm-ostree`: manages package layering/rebase on atomic Fedora systems like Kinoite.
- `bootc`: tooling for building and switching bootable OCI images.
- `cosign`: signs and verifies container images.
- `just`: task runner used by the upstream akmods repository.
- `yq`: YAML processor used to update the upstream akmods target file.

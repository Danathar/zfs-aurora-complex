# zfs-kinoite-containerfile Glossary

This page defines terms used across this repository's docs and workflow comments.

## Core Terms

- `CI`: the GitHub Actions workflows in `.github/workflows`.
- `candidate`: test tag built first on `main` before promotion moves `latest`.
- `stable`: the normal user-facing tag, `latest`.
- `audit tag`: immutable stable tag written during promotion so one published snapshot can be referenced later.
- `artifact`: a file/output saved by a workflow run so you can inspect or reuse it later.
- `manifest`: a structured data file that records what a run produced or which exact inputs it used.
- `workflow`: one named GitHub Actions automation file.
- `workflow run`: one execution of a GitHub Actions workflow from start to finish.
- `pipeline`: the ordered set of jobs/steps in one workflow run.
- `build context`: the set of local files available to the container build.
- `branch-scoped`: a tag/name that includes the branch identifier so branch artifacts stay isolated.
- `Fedora stream` / `kernel stream`: the ongoing flow of new kernel releases over time.
- `tag`: a human-readable image label like `latest` or `candidate-deadbee-43`.
- `image ref`: text that points to a container image, usually `name:tag` or `name@sha256:digest`.
- `digest`: an immutable hash that identifies one exact image content snapshot.
- `namespace`: the owner/org part of an image path, for example `danathar` in `ghcr.io/danathar/zfs-kinoite-containerfile`.
- `rebase` / `rebasing`: switching your installed OS image source to a different container image ref.
- `floating ref`: a tag-based ref such as `:latest` that can point to different content later.
- `digest-pinned ref`: an exact image pointer like `name@sha256:...`; it does not move unless you change the digest.
- `signature`: cryptographic proof that an image digest was signed by a trusted key.
- `sigstore attachment`: the OCI artifact where tools like cosign store image signatures.
- `fail closed`: if a required safety input is missing, stop with an error instead of guessing.
- `stale module` / `stale kmod`: a kernel module built for an older kernel release than the one currently in the base image.
- `hardening`: add safety checks or stricter rules so failures are less likely and easier to catch early.
- `PR`: pull request.
- `VM`: virtual machine.
- `OCI`: Open Container Initiative standards used for container image formats and registries.
- `YAML`: human-readable config format used by GitHub Actions workflows.

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

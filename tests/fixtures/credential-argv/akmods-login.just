# Danathar/akmods `Justfile` recipes in the shapes this check has to read. The
# `login` recipe is the akmods recipe as it was before Danathar/akmods#249 (the
# new instance in #283); `login-stdin` is what that pull request changed it to.
# Not a Justfile anything runs; tests/test_no_credential_in_argv.py reads it to
# check that a recipe's dependencies and the recipes it runs through
# `{{ just }}` are followed, and that a recipe nothing reaches is not reported.

podman := which('podman')

# Login to Registry
[group('Registry')]
@login:
    {{ podman }} login {{ registry }} -u "$REGISTRY_ACTOR"  -p "$REGISTRY_TOKEN"

[group('Registry')]
@login-stdin:
    printf '%s' "$REGISTRY_TOKEN" | {{ podman }} login {{ registry }} -u "$REGISTRY_ACTOR" --password-stdin

build: (cache-kernel-version) && populate
    #!/usr/bin/env bash
    set -euo pipefail
    echo build

cache-kernel-version: (fetch "kernel")
    #!/usr/bin/env bash
    version="$({{ just }} get-kernel-version)"

get-kernel-version:
    skopeo inspect \
        --creds "me:{{ registry_token }}" \
        docker://example

fetch what:
    echo {{ what }}

populate:
    echo ok

unreached:
    podman login -p "$REGISTRY_TOKEN" ghcr.io

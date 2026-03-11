#!/usr/bin/env bash
#
# Script: files/scripts/configure-signing-policy.sh
# What: Writes trust policy for this repository's signed image path.
# Doing: Adds one `policy.json` rule and one `registries.d` discovery file for
#        the final image repository.
# Why: After the first boot into this image family, future `bootc upgrade`
#      operations should verify signatures from this repository automatically.
# Goal: Keep the single-repository signing story boring and predictable.
#
set -euo pipefail

: "${IMAGE_REPO:?Missing IMAGE_REPO}"
: "${SIGNING_KEY_FILENAME:?Missing SIGNING_KEY_FILENAME}"

policy_file="/etc/containers/policy.json"
registries_dir="/etc/containers/registries.d"
key_path="/etc/pki/containers/${SIGNING_KEY_FILENAME}"
registry_file="${registries_dir}/$(basename "${IMAGE_REPO}").yaml"

install -d -m 0755 /etc/pki/containers "${registries_dir}"

POLICY_FILE="${policy_file}" IMAGE_REPO="${IMAGE_REPO}" KEY_PATH="${key_path}" python3 - <<'PY'
import json
import os
from pathlib import Path

policy_path = Path(os.environ["POLICY_FILE"])
image_repo = os.environ["IMAGE_REPO"]
key_path = os.environ["KEY_PATH"]

if policy_path.exists():
    data = json.loads(policy_path.read_text(encoding="utf-8"))
else:
    data = {"default": [{"type": "insecureAcceptAnything"}]}

data.setdefault("transports", {})
data["transports"].setdefault("docker", {})
data["transports"]["docker"][image_repo] = [
    {
        "type": "sigstoreSigned",
        "keyPath": key_path,
        "signedIdentity": {"type": "matchRepository"},
    }
]

policy_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY

cat > "${registry_file}" <<EOF_REG
# Sigstore attachment discovery for the published image repository.
docker:
  ${IMAGE_REPO}:
    use-sigstore-attachments: true
EOF_REG

chmod 0644 "${registry_file}" "${key_path}" "${policy_file}"

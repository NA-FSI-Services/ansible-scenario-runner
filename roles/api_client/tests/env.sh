#!/usr/bin/env bash
# Shared defaults for run.sh and run-all.sh; existing environment values take precedence.

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export TESTS_DIR
export INVENTORY="${INVENTORY:-${TESTS_DIR}/inventory.yml}"
export PLAYBOOK="${PLAYBOOK:-${TESTS_DIR}/test.yml}"
export VAULT_FILE="${VAULT_FILE:-${TESTS_DIR}/vault.yml}"
export LOGS_DIR="${LOGS_DIR:-${TESTS_DIR}/logs}"
export ANSIBLE_VAULT_PASSWORD_FILE="${ANSIBLE_VAULT_PASSWORD_FILE:-${TESTS_DIR}/.vault_pass}"

export EE_IMAGE="${EE_IMAGE:-quay.io/ansible/creator-ee:latest}"
export EE_PULL_POLICY="${EE_PULL_POLICY:-missing}"

# macOS containers run in a VM; Linux host networking reaches the host directly.
# Explicit empty values disable container options or retain the vault API URL.
if [[ "$(uname -s)" == Darwin ]]; then
  export NAVIGATOR_CONTAINER_OPTIONS="${NAVIGATOR_CONTAINER_OPTIONS-}"
  export API_BASE_URL="${API_BASE_URL-http://host.containers.internal:${MOCK_API_PORT:-8765}}"
else
  export NAVIGATOR_CONTAINER_OPTIONS="${NAVIGATOR_CONTAINER_OPTIONS---network=host}"
fi

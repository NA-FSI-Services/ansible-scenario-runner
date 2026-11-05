#!/usr/bin/env bash
# Run one api_client scenario against the mock API (encrypted vault.yml)
# via ansible-navigator (execution environment).
# Usage: ./run.sh <scenario> [verbosity]
#
#   scenario   name (happy-path), file (happy-path.yml), or path
#              (scenarios/happy-path.yml) → tests/scenarios/<name>.yml
#   verbosity  optional 0–5 → ansible -v … -vvvvv (0 = none);
#              also accepts -v / -vv / … / -vvvvv (passed through to navigator)
#
# Playbook / inventory / vault / EE come from env.sh. Override with env vars:
#   PLAYBOOK=... INVENTORY=... VAULT_FILE=... EE_IMAGE=... ./run.sh happy-path
#   API_BASE_URL=http://host.containers.internal:8765 ./run.sh happy-path
#
# EXPECTED_FAILURE=1 accepts any nonzero navigator exit; it does not check the cause.
# Logging: stdout/stderr shown on console; sanitized copy under logs/
#   LOG_FILE=/path/to.log ./run.sh happy-path   # optional explicit path
#   SKIP_LOG=1 ./run.sh happy-path              # disable file logging
set -euo pipefail

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=env.sh
source "${TESTS_DIR}/env.sh"
# shellcheck source=lib/log.sh
source "${TESTS_DIR}/lib/log.sh"

usage() {
  echo "usage: $0 <scenario> [verbosity]" >&2
  echo >&2
  echo "  scenario    name without path (happy-path), file (happy-path.yml)," >&2
  echo "              or path (scenarios/happy-path.yml)" >&2
  echo "  verbosity   optional integer 0–5 (ansible -v … -vvvvv; 0 = none)," >&2
  echo "              or ansible-style -v / -vv / … / -vvvvv (navigator passthrough)" >&2
  echo >&2
  echo "  runner      ansible-navigator run --mode stdout (requires navigator" >&2
  echo "              + container engine; EE image from \$EE_IMAGE)" >&2
  echo "  scenario_vars  escape hatch used by the playbook: this wrapper" >&2
  echo "                 passes -e scenario_vars=scenarios/<name>.yml" >&2
  echo "  playbook    from \$PLAYBOOK (env.sh); override:" >&2
  echo "              PLAYBOOK=/path/to.yml $0 <scenario> [verbosity]" >&2
  echo "  inventory   from \$INVENTORY; vault from \$VAULT_FILE" >&2
  echo "  EE          \$EE_IMAGE / \$EE_PULL_POLICY (see env.sh)" >&2
  echo "  logging     logs/<scenario>_<timestamp>.log (SKIP_LOG=1 to disable)" >&2
  echo >&2
  echo "  scenarios:" >&2
  local f
  for f in "$TESTS_DIR/scenarios"/*.yml; do
    [[ -f "$f" ]] || continue
    printf '    %s\n' "$(basename "$f" .yml)" >&2
  done
}

resolve_scenario() {
  local arg="$1"
  local base candid

  # Strip leading ./ and optional scenarios/ prefix for basename handling
  base="${arg#./}"
  if [[ "$base" == scenarios/* ]]; then
    base="${base#scenarios/}"
  fi
  # Drop trailing .yml / .yaml
  base="${base%.yml}"
  base="${base%.yaml}"

  candid="$TESTS_DIR/scenarios/${base}.yml"
  if [[ -f "$candid" ]]; then
    printf '%s\n' "$candid"
    return 0
  fi

  # Absolute or relative path that already exists
  if [[ -f "$arg" ]]; then
    printf '%s\n' "$(cd "$(dirname "$arg")" && pwd)/$(basename "$arg")"
    return 0
  fi
  if [[ -f "$TESTS_DIR/$arg" ]]; then
    printf '%s\n' "$TESTS_DIR/$arg"
    return 0
  fi

  return 1
}

# Resolve a path relative to TESTS_DIR unless absolute.
resolve_under_tests() {
  local p="$1"
  if [[ "$p" == /* ]]; then
    printf '%s\n' "$p"
  else
    printf '%s\n' "$TESTS_DIR/$p"
  fi
}

verbosity_to_flag() {
  local v="$1"
  case "$v" in
    '' | 0) echo "" ;;
    1 | -v) echo "-v" ;;
    2 | -vv) echo "-vv" ;;
    3 | -vvv) echo "-vvv" ;;
    4 | -vvvv) echo "-vvvv" ;;
    5 | -vvvvv) echo "-vvvvv" ;;
    *)
      echo "invalid verbosity: $v (use 0–5 or -v … -vvvvv)" >&2
      return 1
      ;;
  esac
}

INVENTORY="$(resolve_under_tests "$INVENTORY")"
PLAYBOOK="$(resolve_under_tests "$PLAYBOOK")"
VAULT="$(resolve_under_tests "$VAULT_FILE")"
VAULT_PASS_FILE="$ANSIBLE_VAULT_PASSWORD_FILE"
# Ansible resolves relative password paths from the caller's working directory.
# Preserve that location when the runner changes to TESTS_DIR below.
if [[ "$VAULT_PASS_FILE" != /* ]]; then
  VAULT_PASS_FILE="$PWD/$VAULT_PASS_FILE"
fi
case "${EXPECTED_FAILURE:-0}" in
  0|false|FALSE) EXPECTED_FAILURE=0 ;;
  1|true|TRUE) EXPECTED_FAILURE=1 ;;
  *) echo "EXPECTED_FAILURE must be 0, 1, false, or true" >&2; exit 2 ;;
esac
# Role dir (parent of tests/) must be visible in the EE: test.yml loads
# "{{ playbook_dir }}/.." as the role path.
ROLE_DIR="$(cd "${TESTS_DIR}/.." && pwd)"

SCENARIO_ARG="${1:-}"
VERBOSITY_ARG="${2:-0}"

if [[ -z "$SCENARIO_ARG" || "$SCENARIO_ARG" == -h || "$SCENARIO_ARG" == --help ]]; then
  usage
  exit 2
fi

if ! command -v ansible-navigator >/dev/null 2>&1; then
  echo "ansible-navigator not found in PATH." >&2
  echo "  Install ansible-navigator (and a container engine: podman or docker)," >&2
  echo "  then re-run. This harness does not fall back to ansible-playbook." >&2
  exit 127
fi

if ! SCENARIO="$(resolve_scenario "$SCENARIO_ARG")"; then
  echo "scenario not found: $SCENARIO_ARG" >&2
  echo "  expected under $TESTS_DIR/scenarios/" >&2
  usage
  exit 1
fi

SCENARIO_NAME="$(basename "$SCENARIO" .yml)"

if ! VERBOSE_FLAG="$(verbosity_to_flag "$VERBOSITY_ARG")"; then
  usage
  exit 2
fi

if [[ ! -f "$VAULT" ]]; then
  echo "missing $VAULT — create an encrypted vault first:" >&2
  echo "  cd $TESTS_DIR && ./setup-vault.sh" >&2
  exit 1
fi

if [[ ! -f "$VAULT_PASS_FILE" ]]; then
  echo "missing $VAULT_PASS_FILE — create it then encrypt (see ./setup-vault.sh):" >&2
  echo "  printf '%s\\n' 'your-password' > .vault_pass && chmod 600 .vault_pass" >&2
  exit 1
fi

if [[ ! -f "$PLAYBOOK" ]]; then
  echo "playbook not found: $PLAYBOOK" >&2
  exit 1
fi

if [[ ! -f "$INVENTORY" ]]; then
  echo "inventory not found: $INVENTORY" >&2
  exit 1
fi

cd "$TESTS_DIR"

# Path relative to tests/ so the playbook can include_vars from playbook_dir.
# Absolute path is used only if the scenario file lives outside tests/.
if [[ "$SCENARIO" == "$TESTS_DIR/"* ]]; then
  SCENARIO_VARS_REL="${SCENARIO#"$TESTS_DIR"/}"
else
  SCENARIO_VARS_REL="$SCENARIO"
fi

verbose_args=()
if [[ -n "$VERBOSE_FLAG" ]]; then
  verbose_args=("$VERBOSE_FLAG")
fi

container_args=()
if [[ -n "$NAVIGATOR_CONTAINER_OPTIONS" ]]; then
  # Use --co=VALUE so options starting with -- (e.g. --network=host)
  # are not eaten by argparse as navigator flags.
  container_args=("--co=${NAVIGATOR_CONTAINER_OPTIONS}")
fi

# ansible-navigator run (stdout / CI-friendly). Vault + scenario_vars extras;
# EE sees tests/ via project mount and the role via --eev on ROLE_DIR.
cmd=(
  ansible-navigator run "$PLAYBOOK"
  -i "$INVENTORY"
  --eei "$EE_IMAGE"
  --pp "$EE_PULL_POLICY"
  --mode stdout
  --pae false
  --eev "${ROLE_DIR}:${ROLE_DIR}:Z"
  "${container_args[@]}"
  --vault-password-file "$VAULT_PASS_FILE"
  "${verbose_args[@]}"
  -e @"$VAULT"
  -e "scenario_vars=${SCENARIO_VARS_REL}"
)
# Container-facing address (defaulted on macOS in env.sh), after the vault so it
# takes precedence. An explicit empty API_BASE_URL uses the role/vault/scenario URL.
if [[ -n "${API_BASE_URL:-}" ]]; then
  cmd+=(-e "api_base_url=${API_BASE_URL}")
fi
# macOS launchd agent sockets cannot be bind-mounted into the Podman VM.
# Scope the opt-out to Navigator; preserve other sockets and allow an override
# for engines configured to make this socket available.
if [[ "$(uname -s)" == Darwin && "${NAVIGATOR_KEEP_SSH_AUTH_SOCK:-0}" != 1 ]]; then
  case "${SSH_AUTH_SOCK:-}" in
    /var/run/com.apple.launchd.*/Listeners|/tmp/com.apple.launchd.*/Listeners|/private/tmp/com.apple.launchd.*/Listeners)
      echo "Omitting macOS launchd SSH agent socket for Navigator (NAVIGATOR_KEEP_SSH_AUTH_SOCK=1 to retain)." >&2
      cmd=(env -u SSH_AUTH_SOCK "${cmd[@]}")
      ;;
  esac
fi

rc=0
set +e
if [[ "${SKIP_LOG:-0}" == "1" ]]; then
  "${cmd[@]}"
  rc=$?
else
  LOG_FILE="${LOG_FILE:-$(scenario_log_path "$SCENARIO_NAME")}" || exit 1
  run_with_sanitized_log "$LOG_FILE" -- "${cmd[@]}"
  rc=$?
  echo "log: $LOG_FILE" >&2
  # Logging failures are harness errors, not expected command failures.
  if [[ "$LOG_CAPTURE_SUCCEEDED" != 1 ]]; then exit 1; fi
fi
set -e

# Private suite handoff: report the command exit only after logging succeeds.
# Preflight/logging failures leave no result, so they cannot become expected passes.
if [[ -n "${_SCENARIO_RESULT_FILE:-}" ]]; then
  printf '%s\n' "$rc" >"$_SCENARIO_RESULT_FILE"
fi

if [[ "$EXPECTED_FAILURE" == 1 ]]; then
  if [[ "$rc" == 0 ]]; then
    echo "expected_failure: command succeeded but failure was required ($SCENARIO_NAME)" >&2
    exit 1
  fi
  echo "expected_failure: command failed as expected ($SCENARIO_NAME, exit $rc)" >&2
  exit 0
fi
exit "$rc"

#!/usr/bin/env bash
# Create gitignored vault.yml (ansible-vault encrypted) and .vault_pass.
#
# Usage:
#   ./setup-vault.sh              # ensure .vault_pass + encrypt vault.yml
#   ./setup-vault.sh --demo       # write demo password "demo-vault-pass" if missing
#   VAULT_PASS='secret' ./setup-vault.sh
#
# Typical first-time flow:
#   cp vault.example.yml vault.yml   # optional; setup-vault.sh copies if missing
#   # edit vault.yml (plaintext) if needed
#   ./setup-vault.sh
#   ansible-vault edit vault.yml --vault-password-file .vault_pass
set -euo pipefail
umask 077

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$TESTS_DIR"

EXAMPLE="${TESTS_DIR}/vault.example.yml"
VAULT="${TESTS_DIR}/vault.yml"
PASS_FILE="${TESTS_DIR}/.vault_pass"
DEMO=0
CREATED_PASS_FILE=0

for arg in "$@"; do
  case "$arg" in
    --demo) DEMO=1 ;;
    -h|--help)
      sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "unknown option: $arg" >&2
      exit 2
      ;;
  esac
done

if ! command -v ansible-vault >/dev/null 2>&1; then
  echo "ansible-vault not found in PATH; no credential files were created" >&2
  exit 127
fi
# Reject symlinks and restrict existing files as well as new ones.
for credential in "$PASS_FILE" "$VAULT"; do
  if [[ -L "$credential" || ( -e "$credential" && ! -f "$credential" ) ]]; then
    echo "credential must be a regular file, not a symlink: $credential" >&2
    exit 1
  fi
  if [[ -f "$credential" ]]; then chmod 600 "$credential"; fi
done

if [[ ! -f "$EXAMPLE" ]]; then
  echo "missing $EXAMPLE" >&2
  exit 1
fi

if [[ ! -f "$PASS_FILE" ]]; then
  if [[ -n "${VAULT_PASS:-}" ]]; then
    printf '%s\n' "$VAULT_PASS" > "$PASS_FILE"
  elif [[ "$DEMO" -eq 1 ]]; then
    printf '%s\n' 'demo-vault-pass' > "$PASS_FILE"
  elif [[ -t 0 ]]; then
    if [[ -f "$VAULT" ]] && head -n1 "$VAULT" | grep -q '^\$ANSIBLE_VAULT'; then
      echo "An encrypted vault already exists, but its local password file is missing."
      echo "Enter the existing vault password; this does not create a new password."
      read -r -s -p "Existing vault password: " pw
      echo
    else
      echo "Choose a new password to encrypt your local vault."
      echo "This is separate from the demo API token, which is already configured."
      echo "The password will be saved locally in .vault_pass (excluded from Git)."
      echo "Nothing will appear while you type."
      read -r -s -p "New vault password: " pw
      echo
      if [[ -z "$pw" ]]; then
        echo "empty password not allowed; no credential files were created" >&2
        exit 1
      fi
      read -r -s -p "Confirm vault password: " confirmation
      echo
      if [[ "$pw" != "$confirmation" ]]; then
        echo "passwords do not match; no credential files were created. Run ./setup-vault.sh again." >&2
        exit 1
      fi
      unset confirmation
    fi
    if [[ -z "$pw" ]]; then
      echo "empty password not allowed" >&2
      exit 1
    fi
    printf '%s\n' "$pw" > "$PASS_FILE"
    unset pw
  else
    echo "missing $PASS_FILE — set VAULT_PASS, use --demo, or run interactively" >&2
    exit 1
  fi
  chmod 600 "$PASS_FILE"
  CREATED_PASS_FILE=1
  echo "wrote $PASS_FILE (gitignored)"
else
  echo "using existing $PASS_FILE"
fi

if [[ ! -f "$VAULT" ]]; then
  cp "$EXAMPLE" "$VAULT"
  echo "copied vault.example.yml → vault.yml"
fi

if head -n1 "$VAULT" | grep -q '^\$ANSIBLE_VAULT'; then
  if ! ansible-vault view "$VAULT" --vault-password-file "$PASS_FILE" >/dev/null; then
    echo "could not decrypt vault.yml with $PASS_FILE" >&2
    if [[ "$CREATED_PASS_FILE" == 1 ]]; then
      rm -f "$PASS_FILE"
      echo "removed the new password file; retry with the existing vault password" >&2
    else
      echo "correct the existing password file before retrying" >&2
    fi
    exit 1
  fi
  echo "vault.yml is already encrypted; password verified"
  exit 0
fi

if ! ansible-vault encrypt "$VAULT" --vault-password-file "$PASS_FILE"; then
  echo "encryption failed; $VAULT may still contain plaintext (restricted to owner access)" >&2
  exit 1
fi
echo "encrypted vault.yml"
echo "edit later: ansible-vault edit vault.yml --vault-password-file .vault_pass"

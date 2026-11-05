# API client example

This walkthrough demonstrates the [Ansible Scenario Runner](../README.md)
using a local mock REST API. The API client and mock server are example
components; substitute your own role and target system when adopting the harness.

## Included demo

Collection **`demo.scenarios`** contains one demo role:

- **`api_client`** — calls a Bearer-authenticated REST API via `ansible.builtin.uri`
  (secrets from an ansible-vault–encrypted file; request shape from scenario files)

## Layout

```
galaxy.yml
meta/runtime.yml
roles/api_client/
  defaults/ / tasks/ / meta/
  tests/
    test.yml             # harness playbook (source of truth)
    inventory.yml        # committed localhost inventory
    vault.example.yml    # plaintext key template (committed)
    setup-vault.sh       # copy example → encrypt vault.yml
    env.sh               # EE_IMAGE, vault password file, inventory paths
    lib/log.sh           # sanitized run logging helpers
    scenario-suite.yml   # ordered suite manifest for run-all.sh
    scenarios/
    logs/                # run output (*.log gitignored; .gitkeep committed)
    mock_api/            # stdlib Python mock REST API
    run.sh               # single scenario
    run-all.sh           # full suite from scenario-suite.yml
```

Checkout-and-run artifacts under `roles/api_client/tests/`: **`test.yml`**,
`inventory.yml`, `scenarios/*.yml`, `scenario-suite.yml`, `vault.example.yml`,
`setup-vault.sh`, `env.sh`, `lib/log.sh`, `mock_api/`, `run.sh`, and
`run-all.sh`. Local-only (gitignored): encrypted **`vault.yml`**,
**`.vault_pass`**, and **`logs/*.log`**.

Role FQCN: `demo.scenarios.api_client`

---

## Prerequisites

Install the [framework prerequisites](../README.md#prerequisites). This demo also
uses Python 3 for the mock server, `curl` for its health check, and `flock`
(util-linux) and `ps` for lifecycle locking and process checks. On macOS, install
`flock` with `brew install flock`; `ps` is included with macOS. Container
networking is configured by `NAVIGATOR_CONTAINER_OPTIONS` in `env.sh`.

The playbook loads the role by path, so collection installation is optional.
Start from the repository root after cloning (see [Start here](../README.md#start-here)).
After the vault setup step, stay in
`roles/api_client/tests/` for the remaining commands.

Before starting, check that the host tools are available:

```bash
bash --version
python3 -c 'import yaml; print("PyYAML available")'
ansible-navigator --version
ansible-vault --version
curl --version
command -v flock ps
podman info  # use docker info if Docker is your configured engine
```

On Linux, the default `--network=host` lets the execution environment reach
the host's `127.0.0.1:8765`. On macOS, the default leaves the container network
unchanged and selects the container-facing URL described below. Explicitly setting
`NAVIGATOR_CONTAINER_OPTIONS` (including an empty value) overrides either default.

The default execution environment image is `quay.io/ansible/creator-ee:latest`.
The first run may need to pull it. To use an image already available in your
environment, export `EE_IMAGE` before running the scripts; it must contain the
Ansible dependencies needed by the role.

Navigator starts the execution environment through your configured container
engine; you do not need to start the container separately.

### macOS with Homebrew and Podman

Start your Podman machine (`podman machine start` if it is stopped). Podman runs
containers in a Linux VM, so container loopback and `--network=host` refer to that
VM. Use Podman's [host hostname](https://docs.podman.io/en/latest/markdown/podman-run.1.html#add-host-hostname-ip)
to reach the Mac. Keep the mock API bound to `127.0.0.1`; there is no need to
expose it on `0.0.0.0` or change the vault's loopback URL.

After starting the mock API and setting up the vault below, run from
`roles/api_client/tests/`:

```bash
./run.sh happy-path
./run-all.sh
```

On macOS, `env.sh` automatically defaults `API_BASE_URL` to
`http://host.containers.internal:8765` (or the port in `MOCK_API_PORT` if set).
The runner passes this as an Ansible `api_base_url` extra-var after the vault, so
it takes precedence over the vault URL without modifying the vault file.
Set `API_BASE_URL` to use another address, or set it to an empty value to use
your vault/role/scenario URL: `API_BASE_URL= ./run.sh happy-path`.
Linux keeps its existing URL behavior. Direct host
`ansible-playbook` execution below continues to use the vault's `127.0.0.1` URL.
For a manual Navigator command, pass
`-e api_base_url=http://host.containers.internal:8765` after `-e @vault.yml`.

The runner automatically handles macOS launchd SSH agent sockets that cannot be
mounted in the Podman VM, while preserving other agent sockets. No manual socket
configuration is needed for this demo. Set `NAVIGATOR_KEEP_SSH_AUTH_SOCK=1` only
if your container engine supports forwarding the launchd socket.

To check connectivity independently of Ansible (using an already pulled EE):

```bash
podman run --rm --pull=never quay.io/ansible/creator-ee:latest python3 -c \
  'import urllib.request; print(urllib.request.urlopen("http://host.containers.internal:8765/health", timeout=5).read().decode())'
```

## Tested environment

The example was tested with:

| Component | Version |
| --- | --- |
| Ansible Navigator | 25.12.0 |
| Podman | 5.8.9 |
| Execution image | `quay.io/ansible/creator-ee:latest` |
| Ansible Core in the image | 2.16.3 |

## Walkthrough

### 1. Start the mock API

The mock API runs at `http://127.0.0.1:8765` and uses `demo-token` as its default
Bearer token. The example vault already contains this value, so no credential
changes are needed to run the demo. The server uses only Python's standard
library; no additional Python packages are needed for it.

You can optionally set `MOCK_API_TOKEN` to use a different token. If you do,
set `api_token` in your local vault to the same value.

```bash
roles/api_client/tests/mock_api/manage.sh start   # stop | status | restart
```

Endpoints:

| Method | Path | Auth | Notes |
| --- | --- | --- | --- |
| `GET` | `/health` | none | `{"status":"ok"}` |
| `GET` | `/v1/message` | Bearer | `{"message":"Hello from mock API",...}` |
| `POST` | `/v1/echo` | Bearer | echoes JSON body |

### 2. Create an encrypted vault

`vault.example.yml` documents required keys and stays **plaintext + committed**.
Runtime secrets live in **encrypted** `vault.yml` (gitignored), unlocked via
**`.vault_pass`** (also gitignored).

```bash
cd roles/api_client/tests
./setup-vault.sh
# asks you to choose and confirm a new vault password, then writes .vault_pass,
# copies the example,
# then runs: ansible-vault encrypt vault.yml --vault-password-file .vault_pass
```

When prompted, **choose a new vault password**, then enter it again to confirm.
You do not need an existing password for a fresh checkout. Nothing appears as
you type. The demo API token is already configured in the example vault and
is separate from this password. If the entries differ, the script stops without
creating credential files; run it again to retry.

Later runs reuse `.vault_pass` without prompting. If an encrypted `vault.yml`
already exists but `.vault_pass` is missing, the script asks for that vault's
existing password instead. Setup verifies that the password decrypts the vault.
If verification fails, it removes a password file created during that attempt
so you can retry; a pre-existing password file is retained for correction.

Manual equivalent, from the same tests directory:

```bash
printf '%s\n' 'your-password' > .vault_pass && chmod 600 .vault_pass
cp vault.example.yml vault.yml
# edit api_token / api_base_url in vault.yml if needed
ansible-vault encrypt vault.yml --vault-password-file .vault_pass
```

Edit later: `ansible-vault edit vault.yml --vault-password-file .vault_pass`

The example vault already matches the mock API's default address and token.
The vault password you enter protects the local vault; it is separate from the
API token. The setup script creates credential files with owner-only access.
If encryption fails, it reports the failure and leaves the restricted file
available for correction; do not treat that file as encrypted.
If you change `MOCK_API_TOKEN` or the server address, update the
corresponding vault values too.

If you created a local vault with an earlier checkout, pulling updates does not
change it. To use the current demo default, run
`ansible-vault edit vault.yml --vault-password-file .vault_pass` and set
`api_token: demo-token`, then restart the mock API with `./mock_api/manage.sh restart`.

### 3. Run individual scenarios

```bash
./run.sh happy-path
./run.sh happy-path 2
./run.sh echo-payload -vv
./run.sh scenarios/health-no-auth.yml
./run.sh unauthorized
./run.sh not-found
./run.sh happy-path-check
```

| Scenario | Purpose |
| --- | --- |
| `happy-path` | Validate the authenticated message response |
| `echo-payload` | POST a JSON body and validate the response |
| `health-no-auth` | Exercise the public health endpoint |
| `unauthorized` | Require HTTP 401 and `missing_bearer` when the Bearer header is omitted |
| `not-found` | Require HTTP 404 and `not_found` for a missing endpoint |
| `unknown-path` | Request a missing endpoint while the role expects HTTP 200; the suite expects failure |
| `happy-path-check` | Apply native check mode to the role |

Each run starts the role with an **API client configuration** banner showing
`CHECK MODE (dry run)` or `NORMAL MODE (live requests)` in a bordered block,
followed by the check-mode boolean, scenario, request URL,
HTTP method, and expected status. It does not print the API token, headers, or
request body.

The HTTP request task uses `no_log` to protect credentials even at higher
verbosity. Request failures report the HTTP status and expected status without
printing request headers; status `-1` means no HTTP response was received.
Successful response summaries still include the API's JSON response.

The check-mode scenario sets `scenario_check_mode: true` in its own YAML file.
Ansible skips the `uri` request in check mode. The role also skips response
assertions and the response summary, so this case does not validate a live API
response. A message explicitly confirms that no API call was made and response
assertions were skipped. Use the normal scenarios for live API validation.

The `unknown-path` scenario requests a missing endpoint while the role expects
HTTP 200. The manifest marks that case with `expected_failure: true`, so the
nonzero command exit counts as a pass. To use the same expectation individually:

```bash
EXPECTED_FAILURE=1 ./run.sh unknown-path
```

Running `./run.sh unknown-path` without that setting returns failure, because
single runs do not read the manifest.

The `unauthorized` and `not-found` scenarios validate exact HTTP responses through
ordinary Ansible assertions. They fail on connection errors or unexpected status
codes and do not use `expected_failure`.

### 4. Run the suite and review evidence

```bash
./run-all.sh
./run-all.sh --clean-logs
```

Each run opens with a **SCENARIO SUITE** banner showing the manifest, number of
scenarios, execution image, and log directory. No credential values are shown.
The banner is also captured in the suite summary log.

The second command removes existing `logs/*.log` before running. The suite
includes all seven scenarios above. `unknown-path` passes when the command exits
nonzero. This does not verify why it failed: inspect its log for the expected
HTTP 404 rather than a connection, authentication, or container error.
The suite summary shows the actual outcomes, marking the expected failure:

```text
PASS happy-path
PASS echo-payload
PASS health-no-auth
PASS unauthorized
PASS not-found
FAIL unknown-path (expected)
PASS happy-path-check

passed=6 failed=1
```

The failed run remains visible in the count. The suite returns exit status 0
because that failure was expected and there were no unexpected outcomes.
The check-mode case passes without sending an API request, as described above.
Review the timestamped scenario logs and suite summary in `logs/`.

If a case fails, open the scenario log printed by the runner. Check that the
mock API is running (`./mock_api/manage.sh status`), the vault values match it,
and the execution environment can reach the API. For a case marked
`expected_failure: true`, even a container startup or image pull error can count
as a pass because only the command exit status is checked.

See the [runner reference](../README.md#running-tests-and-reading-results) for
verbosity, environment overrides, logging, and direct Ansible invocation.

### 5. Run a scenario with plain Ansible (without the runner)

The runner scripts are optional. The same test playbook, inventory, and scenario
variables can be used directly with `ansible-playbook`. This does **not** invoke
`run.sh`, `run-all.sh`, or `ansible-navigator`.

With the mock API still running and the vault configured, from
`roles/api_client/tests/` execute:

```bash
ansible-playbook -i inventory.yml test.yml \
  --vault-password-file .vault_pass \
  -e @vault.yml \
  -e scenario_vars=scenarios/happy-path.yml
```

- **Playbook:** `test.yml` loads the selected scenario and invokes the role.
- **Inventory:** `inventory.yml` supplies the target (localhost in this demo).
- **Vault:** `.vault_pass` decrypts `vault.yml`, which supplies the API credentials.
- **Scenario:** `scenario_vars` points to the scenario's inputs and expected values.

Change `scenarios/happy-path.yml` to another scenario file to run a different
case. Normal Ansible failures return a nonzero exit status; the runner's
`expected_failure: true` handling does not apply to this direct command.

Direct execution requires `ansible-playbook` and the role's dependencies installed
on the host; the execution environment image is not used. The underlying role
and assertions remain the same, but you do not get the runner's timestamped logs,
ordered suite execution, or suite-level summaries.

### 6. Stop the mock API

From the tests directory:

```bash
./mock_api/manage.sh stop
```

The launcher verifies the recorded process identity before stopping it and
waits for shutdown before restarting. Old PID-only records cannot establish
ownership and are not used to signal a process. If upgrading from an earlier
checkout with the mock API still running, stop it with the old launcher first.

### API client variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `api_base_url` | `http://127.0.0.1:8765` | API root (usually from vault) |
| `api_token` | `""` | Bearer token (**required**; from vault) |
| `api_path` | `/v1/message` | Path appended to base URL |
| `api_method` | `GET` | HTTP method |
| `api_body` | `{}` | JSON body (non-GET) |
| `api_headers` | `{}` | Additional headers; values override the default Bearer header |
| `api_expected_status` | `200` | Passed to `uri` `status_code` |
| `api_message_field` | `message` | JSON field to assert when expected message set |
| `api_expected_message` | `""` | If non-empty, assert field equals this |

Without credentials the role fails with a clear assert pointing at
`./setup-vault.sh` / `vault.example.yml`.

## Collection install (optional)

To use the FQCN instead of a role path, install or link this repo as
`ansible_collections/demo/scenarios` on `ANSIBLE_COLLECTIONS_PATH`, then set
the playbook role to `demo.scenarios.api_client`.

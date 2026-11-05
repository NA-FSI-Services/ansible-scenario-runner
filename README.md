# ansible-scenario-runner

A consistent way for teams to test Ansible automation before handing it off for
use in larger workflows. Keep test scenarios alongside the code so anyone on
the team can run them and review the results.

## Motivation

This runner grew out of a client project developing Ansible automation for
disaster recovery and business continuity. One part of the team built the
automation, while another incorporated it into workflows in Ansible Automation
Platform (AAP). Developers needed a practical way to test their work thoroughly
and make those tests repeatable by teammates.

The aim was to bring day-to-day development and testing together in the role’s
conventional `tests/` directory. Scenarios could be developed there, kept alongside
the automation in Git, and reused without maintaining a separate set of
development commands. A developer could run one scenario while making changes,
then run an ordered suite to check existing behavior.

Capturing clean, readable output was equally important. Test results had to be
collected and uploaded to Confluence as evidence. Producing log files as part of
each run made that handoff easier, while prepared scenarios also made client
demonstrations more predictable.

Together, these conventions offered a shared testing routine that the team could
adopt in place of individual approaches.

### Why not Molecule?

Molecule was considered for role testing, but it introduced friction in the
client environment and more complexity than this use case required. The goal
here was a lightweight approach built on ordinary Ansible playbooks, variables,
and execution conventions, with shell scripts only for repeatable runs and log
capture. This runner is not intended to replace Molecule where it is a good fit.

## Start here

```bash
git clone https://github.com/NA-FSI-Services/ansible-scenario-runner.git
cd ansible-scenario-runner
```

The repository includes a small API client and a local mock server so you can
try the testing pattern without access to the original project or an AAP
installation. The demo contains no DR or failover automation.

1. Check the [prerequisites](#prerequisites).
2. Follow the [API client walkthrough](docs/api-client-example.md) to start the
   mock API, create a local vault, run the scenarios, and inspect the results.
   That walkthrough uses files already included in this checkout.
3. Read [How it works](#how-it-works) and
   [Scenario configuration](#scenario-configuration) to understand the harness.
4. When ready, follow [Using the framework with your own role](#using-the-framework-with-your-own-role).

The runner reference below uses placeholder scenarios such as `baseline`.
For your first run, use the walkthrough's `happy-path` example instead.

## How it works

The framework lives in each role's `tests/` directory. Its reusable scripts
currently reside under `roles/api_client/tests/` in this repository, alongside
the worked example.

1. `run-all.sh` reads the suite manifest and calls `run.sh` in the declared order.
2. `run.sh` invokes `ansible-navigator` with the execution context and scenario path.
3. `test.yml` loads scenario variables and invokes the enclosing role, applying
   the scenario's native check-mode setting.
4. The runner compares the command exit status with the expected outcome. Each
   run produces a timestamped log. Suite runs continue across failures and
   produce a pass/fail summary.

The baseline is one playbook and one inventory, with explicit overrides for
cases that need a different execution context. The wrappers are optional:
the playbook and scenario files can also be used directly with Ansible.

These are role-level scenario tests. Depending on what a role calls or changes,
they can exercise integration behavior as well as individual automation logic.
The scenarios and assertions determine the coverage; the runner does not test
AAP workflow orchestration.

### Framework files

The following layout is the pattern to use inside your own role:

```text
roles/<your-role>/tests/
  run.sh                 # run one scenario
  run-all.sh             # run an ordered suite
  env.sh                 # paths and execution environment
  lib/log.sh             # log capture and text cleanup
  test.yml               # load scenario variables and invoke the role
  inventory.yml          # default target inventory
  scenario-suite.yml     # ordered cases, expected outcomes, execution overrides
  scenarios/*.yml        # role inputs, expected results, check-mode setting
  vault.example.yml      # credential template without real secrets
  setup-vault.sh         # create the local encrypted vault
  .gitignore             # exclude credentials and runtime artifacts
  logs/.gitkeep          # retain the empty log directory in Git
```

Commit the harness, scenarios, inventory, and credential template. Keep the
local `vault.yml`, `.vault_pass`, and runtime logs out of Git. The supplied
scripts currently require a vault file and password file for each run.

### Prerequisites

- Bash with `mapfile` support (Bash 4 or later) and Python 3, with PyYAML
  installed for the host Python interpreter that runs the suite.
- `ansible-navigator` and a container engine such as Podman or Docker.
- An Ansible execution environment image containing the role's dependencies.
- `ansible-vault` for local vault setup.

Versioned inputs make reruns easier to reproduce. Results still depend on the
target system, secrets, and execution environment. Pin the environment image
when consistent dependencies matter. See the demo’s
[tested environment](docs/api-client-example.md#tested-environment) for a
known-working setup.

## Scenario configuration

### Inputs and assertions

A scenario is a YAML variables file under `scenarios/`. It supplies the role's
inputs and any expected values used by your assertions. The role or test
playbook owns the assertion logic; the runner uses Ansible's exit status.

`run.sh` passes the scenario path as `scenario_vars`. The supplied `test.yml`
resolves it relative to the playbook directory, checks that it exists and stays
under that directory, then loads it with `include_vars`. Credentials are loaded
separately from the vault.

### Native check mode

Set this variable in a scenario file to apply Ansible's native check mode to
the role:

```yaml
scenario_check_mode: true
# Add the role's inputs and expected values here.
```

Use a filename qualifier such as `-check` to make the intent visible. The
variable controls execution; the filename is only a convention. Single runs
and suite runs load the same setting without additional shell flags.

After loading the scenario, the playbook applies this expression to the role:

```yaml
check_mode: "{{ (scenario_check_mode | default(false)) or ansible_check_mode }}"
```

Omitting the variable or setting it to `false` uses normal execution unless
Ansible was explicitly invoked with `--check`. Use YAML booleans `true` and
`false`, not quoted strings. The built-in `ansible_check_mode` reflects the CLI
flag; the scenario variable selects the role's keyword setting.

Check-mode behavior depends on the modules used by the role. Some predict
changes; others skip execution. Assertions that depend on skipped tasks must
account for missing results. Adapted playbooks must retain the variable loading,
validation, and role keyword if they need this behavior.

### Suite manifest and negative scenarios

`scenario-suite.yml` declares defaults and an ordered list of cases. This
illustrative manifest assumes matching scenario files exist:

```yaml
defaults:
  playbook: test.yml
  inventory: inventory.yml
  vault_file: vault.yml
  verbosity: 0

scenarios:
  - name: baseline
  - name: baseline-check
  - name: invalid-input
    expected_failure: true
  - name: alternate-target
    scenario: scenarios/baseline.yml
    inventory: inventory-alternate.yml
```

| Entry field | Behavior |
| --- | --- |
| `name` | Required case name; default vars file is `scenarios/<name>.yml` |
| `scenario` or `file` | Explicit scenario vars file |
| `playbook` | Override the default test playbook |
| `inventory` | Override the default inventory |
| `vault_file` | Override the default vault |
| `verbosity` | Override verbosity, from 0 to 5 |
| `expected_failure` | YAML boolean; `true` accepts a nonzero command exit and rejects success; default `false` |

Manifest paths resolve relative to the role's `tests/` directory unless
absolute. Check mode belongs in the scenario variables file, not the manifest.
For a normal case, a zero command exit is a pass and a nonzero exit is a failure.
With `expected_failure: true`, a nonzero `ansible-navigator` exit is a pass and a
zero exit is an unexpected success, which fails the case. No task names or error
matchers are required.

This checks the exit status, not the cause of failure. A container startup
error, vault decryption error, or unrelated Ansible failure can also count as
an expected failure. Inspect the scenario log to confirm the reason. When you
need to verify a particular error, assert it in the role or test playbook and
run that case with `expected_failure` omitted or `false`.

Runner preflight errors (such as missing files or invalid arguments) and log
capture failures remain failures; they are not inverted. Direct Ansible
invocations return Ansible's original exit status because expected-outcome
handling belongs to the shell runner.

## Running tests and reading results

Run commands from your role's `tests/` directory. The examples below use
`baseline` as a placeholder for a scenario you have created.

### Single scenario

```bash
./run.sh baseline
./run.sh baseline 2
./run.sh scenarios/baseline.yml -vv
./run.sh invalid-input
```

The interface is `./run.sh <scenario> [verbosity]`. A scenario can be a name,
filename, or path. Verbosity accepts `0`–`5` or `-v` through `-vvvvv`.
A normal run returns Ansible's exit code, or a nonzero status if logging fails.
Single runs do not read the suite manifest. To mark a single run as expected
to fail:

```bash
EXPECTED_FAILURE=1 ./run.sh invalid-input
```

This uses the same exit-status rule as `expected_failure: true` in the manifest.

### Full suite

```bash
./run-all.sh
./run-all.sh scenario-suite.yml
./run-all.sh --clean-logs
```

Before the first scenario, a short banner shows the manifest, scenario count,
execution image, and log directory for that run. It uses the effective values,
including environment overrides, and is also saved in the suite summary log.

The suite runs every case in manifest order without stopping for individual
failures. The summary counts successful runs as `passed` and failed runs as
`failed`, whether or not failure was expected. Expected failures are labelled
`FAIL <name> (expected)`. An unexpected success is labelled
`PASS <name> (unexpected—failure expected)` and still counts as passed.
Runner errors are labelled `FAIL <name> (runner error)` and count as failed.
The suite returns nonzero if a result differs from its expected outcome or a
runner error occurs. Thus the demo reports `passed=6 failed=1` and exits
successfully because its one failure was expected.

The entire manifest is validated before
any scenario runs or existing logs are cleaned. Add new scenario files
and manifest entries to expand regression coverage without changing the scripts.

### Execution context

`env.sh` supplies defaults. Override these variables before invoking a runner:

| Variable | Purpose |
| --- | --- |
| `PLAYBOOK`, `INVENTORY` | Test playbook and target inventory |
| `VAULT_FILE` | Encrypted variables file |
| `ANSIBLE_VAULT_PASSWORD_FILE` | Password-file path; relative overrides resolve from the caller's directory |
| `EE_IMAGE` | Execution environment image |
| `LOGS_DIR` | Output directory, default `logs/` |
| `LOG_FILE` | Explicit single-run log path |
| `SKIP_LOG` | Set to `1` to disable scenario log capture |

For example:

```bash
EE_IMAGE=localhost/my-ansible-ee:latest ./run.sh baseline
PLAYBOOK=alternate-test.yml INVENTORY=alternate-inventory.yml ./run.sh baseline
```

The bundled image default is `quay.io/ansible/creator-ee:latest`. Select an image
appropriate for your role. Suite manifest defaults and entry overrides determine
the playbook, inventory, vault, and verbosity passed to each scenario.

The pull policy and container options remain runtime defaults in
[`env.sh`](roles/api_client/tests/env.sh); you normally do not need to change
them for the demo. They are omitted from the suite banner. The
[walkthrough](docs/api-client-example.md#prerequisites) explains the demo's
networking setup.

### Logs

| Artifact | Location |
| --- | --- |
| Scenario log | `logs/<scenario>_<UTC-timestamp>.log` |
| Suite summary | `logs/suite-summary_<UTC-timestamp>.log` |
| Logs from one suite | Share a UTC timestamp |

For example: `happy-path_20261025T234509Z.log`. If that filename already exists,
the runner adds `_2`, `_3`, and so on before `.log`. Files are reserved atomically
so simultaneous runs cannot overwrite each other's logs. An explicit `LOG_FILE`
is caller-managed and appends to that path.

Scenario logs contain only cleaned command output, including Ansible's role
banner, task results, errors, and recap. The harness adds no command header,
timestamps, or exit-status footer inside these files. Scenario names and UTC
timestamps remain in the filenames; suite bookkeeping stays in the separate
suite summary. Use these files as review and handoff evidence.
Failure to write a requested log fails the run.

`--clean-logs`, or `CLEAR_LOGS=1`, removes existing `logs/*.log` before a suite
run while retaining the directory and `.gitkeep`. Text cleanup strips terminal
formatting, framed Ansible Runner event metadata, and control characters, and
normalizes line endings. It does not
redact secrets. Console output remains unchanged.

### Direct Ansible invocation

Use `./run.sh baseline` for Navigator execution with the runner's platform,
mount, and logging settings. If the host has Ansible and the role's dependencies,
you can run the playbook directly from your role's tests directory:

```bash
ansible-playbook -i inventory.yml test.yml \
  --vault-password-file .vault_pass \
  -e @vault.yml \
  -e scenario_vars=scenarios/baseline.yml
```

## Using the framework with your own role

1. Copy the framework files listed above from `roles/api_client/tests/` into
   your role's `tests/` directory, including `lib/log.sh` and `.gitignore`.
2. Adapt `test.yml` for your target hosts and role assertions. The supplied
   playbook targets localhost and loads its enclosing role by path. Preserve
   scenario loading and check-mode handling.
3. Set your inventory, execution environment, and container options in `env.sh`.
4. Replace `vault.example.yml` with your credential template and run
   `./setup-vault.sh` to create the local encrypted vault and password file.
5. Create your scenario files and an ordered `scenario-suite.yml`. Run each
   new case individually, then run the full suite and inspect its logs.

The reusable harness is currently distributed inside the example role. Copy
only the framework files; provide your own role inputs, assertions, and any
required test services. Each role owns its test directory and execution context.

## Worked example: API client

The included `demo.scenarios.api_client` role illustrates the framework with a
local mock REST API, encrypted credentials, normal and negative scenarios, and
a check-mode variant.

Follow the [API client walkthrough](docs/api-client-example.md) for setup,
runnable commands, scenario details, and the API variable reference.

## Finding your way through the code

Start with one scenario and follow it through the harness:

1. [happy-path.yml](roles/api_client/tests/scenarios/happy-path.yml) supplies
   the inputs and expected response.
2. [test.yml](roles/api_client/tests/test.yml) loads those variables, validates
   the check-mode setting, and invokes the enclosing role.
3. [tasks/main.yml](roles/api_client/tasks/main.yml) makes the API request and
   asserts the result. This is example role logic, which you replace when
   adopting the harness.
4. [run.sh](roles/api_client/tests/run.sh) assembles the execution command using
   [env.sh](roles/api_client/tests/env.sh) and captures output through
   [lib/log.sh](roles/api_client/tests/lib/log.sh).
5. [run-all.sh](roles/api_client/tests/run-all.sh) validates
   [scenario-suite.yml](roles/api_client/tests/scenario-suite.yml), runs each
   entry, and summarizes the results.

## Testing the runner itself

The demo suite in `roles/api_client/tests/` tests the example API client.
The Python tests in the top-level `runner_tests/` directory check the harness
itself, using temporary directories and a stub navigator
for shell-runner tests. They do not require the mock API or a container image.

From the repository root, with Python 3, PyYAML, and Bash available:

```bash
python3 -m unittest discover -s runner_tests -v
```

Install `ansible-playbook` and `ansible-galaxy` on the host to run all checks.
Without them, the corresponding tests are reported as skipped, so an `OK`
result alone does not mean every check ran.

The tests use an isolated temporary role to verify that check mode predicts
file changes without writing, normal mode writes, the CLI check flag remains
effective, and invalid scenario settings fail before the role runs. Runner
regression tests check manifest validation, exit statuses, and log failures
using a stub navigator. Packaging tests verify that local credentials and
runtime logs are excluded from collection archives. Helper tests exercise mock
API lifecycle handling, vault file permissions, and unique log allocation.
The lifecycle tests require `flock`, `ps`, and `curl`, as does the demo launcher.

GitHub Actions runs these tests on Linux and macOS with Ansible Core 2.16 and
a current release. CI uses a stub Navigator and real host Ansible; the live
Podman execution environment is checked separately with the demo suite.

"""Regression tests for runner outcomes and distributable collection contents."""
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.harness = self.root / 'role/tests'
        shutil.copytree(ROOT / 'roles/api_client/tests', self.harness,
                        ignore=shutil.ignore_patterns('vault.yml', '.vault_pass', '*.log', '.mock_api.pid'))
        (self.harness / 'vault.yml').write_text('{}')
        (self.harness / '.vault_pass').write_text('dummy')
        bins = self.root / 'bin'
        bins.mkdir()
        nav = bins / 'ansible-navigator'
        nav.write_text('#!/bin/sh\necho invoked >> "$PROBE"\necho command-output\nexit "${STUB_RC:-0}"\n')
        nav.chmod(0o755)
        self.probe = self.root / 'invocations'
        self.env = dict(os.environ, PATH=str(bins) + ':' + os.environ['PATH'],
                        PROBE=str(self.probe), LOGS_DIR=str(self.root / 'logs'))
        for key in ('EXPECTED_FAILURE', 'SKIP_LOG', 'LOG_FILE', 'PLAYBOOK', 'INVENTORY',
                    'VAULT_FILE', 'ANSIBLE_VAULT_PASSWORD_FILE', 'CLEAR_LOGS',
                    'API_BASE_URL', 'MOCK_API_PORT'):
            self.env.pop(key, None)

    def run_script(self, script, *args, **env):
        return subprocess.run([str(self.harness / script), *args],
                              env=dict(self.env, **env), capture_output=True, text=True)

    def suite(self, content):
        manifest = self.root / 'suite.yml'
        manifest.write_text(content)
        return self.run_script('run-all.sh', str(manifest))

    def test_platform_network_defaults_and_explicit_overrides(self):
        uname = self.root / 'bin/uname'
        for platform, default in (('Linux', '--network=host'), ('Darwin', '')):
            uname.write_text(f'#!/bin/sh\necho {platform}\n')
            uname.chmod(0o755)
            for override in (None, '', '--network=bridge'):
                with self.subTest(platform=platform, override=override):
                    env = dict(self.env)
                    env.pop('NAVIGATOR_CONTAINER_OPTIONS', None)
                    if override is not None:
                        env['NAVIGATOR_CONTAINER_OPTIONS'] = override
                    result = subprocess.run(
                        ['bash', '-c', 'source "$1"; printf "%s" "$NAVIGATOR_CONTAINER_OPTIONS"',
                         'bash', str(self.harness / 'env.sh')],
                        env=env, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, default if override is None else override)

    def test_api_url_override_and_ssh_agent_are_scoped_to_invocation(self):
        nav = self.root / 'bin/ansible-navigator'
        nav.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$PROBE"\n'
                       'printf "agent=%s\\n" "${SSH_AUTH_SOCK-unset}"\n')
        self.env['API_BASE_URL'] = ''  # Explicitly use the vault URL on any OS.
        self.env['SSH_AUTH_SOCK'] = '/tmp/example-agent.sock'
        result = self.run_script('run.sh', 'happy-path', SKIP_LOG='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('api_base_url=', self.probe.read_text())
        self.assertIn('agent=/tmp/example-agent.sock', result.stdout)

        self.env.pop('SSH_AUTH_SOCK')  # Same environment as env -u SSH_AUTH_SOCK.
        url = 'http://host.containers.internal:8765'
        result = self.run_script('run.sh', 'happy-path', SKIP_LOG='1', API_BASE_URL=url)
        self.assertEqual(result.returncode, 0, result.stderr)
        args = self.probe.read_text().splitlines()
        self.assertEqual(args[-2:], ['-e', f'api_base_url={url}'])
        self.assertGreater(args.index(f'api_base_url={url}'),
                           args.index('@' + str(self.harness / 'vault.yml')))
        self.assertIn('agent=unset', result.stdout)

    def test_platform_api_url_defaults_and_overrides_reach_navigator(self):
        nav = self.root / 'bin/ansible-navigator'
        nav.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$PROBE"\n')
        uname = self.root / 'bin/uname'
        for platform, port, override, expected in (
                ('Linux', None, None, None),
                ('Darwin', None, None, 'http://host.containers.internal:8765'),
                ('Darwin', '9876', None, 'http://host.containers.internal:9876'),
                ('Darwin', None, '', None),
                ('Darwin', None, 'https://api.example.test', 'https://api.example.test'),
                ('Linux', None, 'https://api.example.test', 'https://api.example.test')):
            with self.subTest(platform=platform, port=port, override=override):
                uname.write_text(f'#!/bin/sh\necho {platform}\n')
                uname.chmod(0o755)
                env = {'SKIP_LOG': '1'}
                if port is not None:
                    env['MOCK_API_PORT'] = port
                if override is not None:
                    env['API_BASE_URL'] = override
                result = self.run_script('run.sh', 'happy-path', **env)
                self.assertEqual(result.returncode, 0, result.stderr)
                args = self.probe.read_text().splitlines()
                urls = [arg for arg in args if arg.startswith('api_base_url=')]
                self.assertEqual(urls, [] if expected is None else [f'api_base_url={expected}'])

    def test_only_macos_launchd_agent_is_automatically_omitted(self):
        nav = self.root / 'bin/ansible-navigator'
        nav.write_text('#!/bin/sh\nprintf "%s" "${SSH_AUTH_SOCK-unset}"\n')
        uname = self.root / 'bin/uname'
        for platform, socket, keep, expected in (
                ('Darwin', '/var/run/com.apple.launchd.example/Listeners', '0', 'unset'),
                ('Darwin', '/private/tmp/com.apple.launchd.example/Listeners', '0', 'unset'),
                ('Darwin', '/tmp/com.apple.launchd.example/Listeners', '0', 'unset'),
                ('Darwin', '/tmp/custom-agent.sock', '0', '/tmp/custom-agent.sock'),
                ('Linux', '/var/run/com.apple.launchd.example/Listeners', '0',
                 '/var/run/com.apple.launchd.example/Listeners'),
                ('Darwin', '/var/run/com.apple.launchd.example/Listeners', '1',
                 '/var/run/com.apple.launchd.example/Listeners')):
            with self.subTest(platform=platform, socket=socket, keep=keep):
                uname.write_text(f'#!/bin/sh\necho {platform}\n')
                uname.chmod(0o755)
                result = self.run_script('run.sh', 'happy-path', SKIP_LOG='1',
                                         SSH_AUTH_SOCK=socket, NAVIGATOR_KEEP_SSH_AUTH_SOCK=keep)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, expected)

    def test_invalid_manifests_never_execute_partial_suite(self):
        for content in ('scenarios: []', 'scenarios: [', '[]',
                        'defaults: []\nscenarios: [{name: happy-path}]',
                        'scenarios:\n - name: happy-path\n - missing_name: true',
                        'scenarios: [{name: happy-path, verbosity: 9}]',
                        'scenarios: [{name: happy-path, expected_failure: invalid}]',
                        'scenarios: [{name: happy-path}, {name: happy-path}]'):
            with self.subTest(content=content):
                result = self.suite(content)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertFalse(self.probe.exists())

    def test_suite_banner_shows_effective_settings_without_runtime_details(self):
        self.env['EE_IMAGE'] = 'localhost/review-ee:test'
        self.env['EE_PULL_POLICY'] = 'never'
        self.env['NAVIGATOR_CONTAINER_OPTIONS'] = '--network=host'
        result = self.suite('scenarios: [{name: happy-path}]')
        self.assertEqual(result.returncode, 0, result.stderr)
        banner = result.stdout.split('=== [happy-path] ===')[0]
        self.assertIn('SCENARIO SUITE', banner)
        self.assertIn(f'Manifest        : {self.root / "suite.yml"}', banner)
        self.assertIn('Scenarios       : 1', banner)
        self.assertIn('Execution image : localhost/review-ee:test', banner)
        self.assertIn(f'Logs directory  : {self.root / "logs"}', banner)
        self.assertNotIn('Pull policy', banner)
        self.assertNotIn('--network=host', banner)
        self.assertNotIn('vault', banner.lower())
        summary = next((self.root / 'logs').glob('suite-summary_*.log')).read_text()
        self.assertIn('SCENARIO SUITE', summary)
        self.assertIn('Execution image : localhost/review-ee:test', summary)

    def test_suite_runs_all_cases_after_failure(self):
        result = self.suite('scenarios: [{name: happy-path}, {name: echo-payload}]')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.probe.read_text().splitlines()), 2)
        self.env['STUB_RC'] = '125'
        result = self.suite('scenarios: [{name: happy-path}, {name: echo-payload}]')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('passed=0 failed=2', result.stdout)

    def test_suite_expected_failure_counts_and_unexpected_success(self):
        manifest = 'scenarios: [{name: unknown-path, expected_failure: true}]'
        self.env['STUB_RC'] = '2'
        result = self.suite(manifest)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('FAIL unknown-path (expected)', result.stdout)
        self.assertIn('passed=0 failed=1', result.stdout)
        self.assertNotIn('unexpected_outcomes=', result.stdout)
        summary = next((self.root / 'logs').glob('suite-summary_*.log')).read_text()
        self.assertIn('# failed: 1', summary)
        self.assertIn('FAIL unknown-path (expected)', summary)
        self.env['STUB_RC'] = '0'
        result = self.suite(manifest)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('passed=1 failed=0', result.stdout)
        self.assertIn('PASS unknown-path (unexpected—failure expected)', result.stdout)

    def test_suite_continues_after_unexpected_success_and_preflight_failure(self):
        result = self.suite('scenarios: [{name: unknown-path, expected_failure: true}, {name: happy-path}]')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('passed=2 failed=0', result.stdout)
        self.assertEqual(len(self.probe.read_text().splitlines()), 2)
        result = self.suite('scenarios: [{name: missing-file, expected_failure: true}, {name: happy-path}]')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('FAIL missing-file (runner error)', result.stdout)
        self.assertIn('passed=1 failed=1', result.stdout)

    def test_log_failure_prevents_execution(self):
        result = self.run_script('run.sh', 'happy-path', LOG_FILE=str(self.root / 'missing/run.log'))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.probe.exists())

    def test_relative_vault_password_path_survives_runner_directory_change(self):
        nav = self.root / 'bin/ansible-navigator'
        nav.write_text('''#!/usr/bin/env python3
from pathlib import Path
import os, sys
path = Path(sys.argv[sys.argv.index('--vault-password-file') + 1])
Path(os.environ['PROBE']).write_text(str(path))
sys.exit(0 if path.is_absolute() and path.read_text() == 'test-password' else 2)
''')
        password = self.root / 'custom password'
        password.write_text('test-password')
        for value in (password.name, str(password)):
            with self.subTest(value=value):
                result = subprocess.run([str(self.harness / 'run.sh'), 'happy-path'],
                                        cwd=self.root, env=dict(self.env, ANSIBLE_VAULT_PASSWORD_FILE=value),
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(Path(self.probe.read_text()).resolve(), password.resolve())

    def test_log_failure_after_execution_is_reported(self):
        # Simulate a sanitizer failure after the command has executed.
        with (self.harness / 'lib/log.sh').open('a') as file:
            file.write('\nsanitize_stream() { if [[ -f "$PROBE" ]]; then return 1; else cat; fi; }\n')
        result = self.run_script('run.sh', 'happy-path')
        self.assertTrue(self.probe.exists())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('failed to capture run evidence', result.stderr)

    def test_command_status_and_logs_are_preserved(self):
        for rc in (0, 2, 125):
            with self.subTest(rc=rc):
                log = self.root / f'run-{rc}.log'
                result = self.run_script('run.sh', 'happy-path', STUB_RC=str(rc), LOG_FILE=str(log))
                self.assertEqual(result.returncode, rc, result.stderr)
                self.assertEqual(log.read_text(), 'command-output\n')

    def test_expected_failure_accepts_nonzero_command_exits(self):
        for skip_log in ('0', '1'):
            for rc in ('2', '125'):
                with self.subTest(rc=rc, skip_log=skip_log):
                    result = self.run_script('run.sh', 'unknown-path', EXPECTED_FAILURE='1', STUB_RC=rc, SKIP_LOG=skip_log)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn('command failed as expected', result.stderr)

    def test_expected_failure_rejects_success(self):
        result = self.run_script('run.sh', 'unknown-path', EXPECTED_FAILURE='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('command succeeded', result.stderr)

    def test_expected_failure_does_not_hide_harness_errors(self):
        result = self.run_script('run.sh', 'unknown-path', EXPECTED_FAILURE='1', STUB_RC='2',
                                 LOG_FILE=str(self.root / 'missing/run.log'))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.probe.exists())
        (self.harness / 'vault.yml').unlink()
        self.assertNotEqual(self.run_script('run.sh', 'unknown-path', EXPECTED_FAILURE='1').returncode, 0)

    def test_expected_failure_does_not_hide_post_command_log_errors(self):
        with (self.harness / 'lib/log.sh').open('a') as file:
            file.write('\nsanitize_stream() { if [[ -f "$PROBE" ]]; then return 1; else cat; fi; }\n')
        result = self.run_script('run.sh', 'unknown-path', EXPECTED_FAILURE='1', STUB_RC='2')
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.probe.exists())

    @unittest.skipUnless(shutil.which('ansible-galaxy'), 'ansible-galaxy required')
    def test_collection_excludes_local_credentials_and_logs(self):
        source = self.root / 'collection'
        source.mkdir()
        shutil.copy(ROOT / 'galaxy.yml', source)
        shutil.copy(ROOT / 'README.md', source)
        shutil.copytree(self.root / 'role', source / 'roles/api_client')
        local = source / 'roles/api_client/tests'
        (local / 'logs/probe.log').write_text('dummy')
        (local / 'mock_api/.mock_api.pid').write_text('123')
        (source / 'ansible-navigator.log').write_text('dummy runtime log')
        (local / 'ansible-navigator.log').write_text('dummy nested runtime log')
        result = subprocess.run(['ansible-galaxy', 'collection', 'build', str(source),
                                 '--output-path', str(self.root)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        with tarfile.open(next(self.root.glob('*.tar.gz'))) as archive:
            names = archive.getnames()
        for suffix in ('/vault.yml', '/.vault_pass', '/probe.log', '/.mock_api.pid', 'ansible-navigator.log'):
            self.assertFalse(any(name.endswith(suffix) for name in names), suffix)
        self.assertIn('roles/api_client/tests/vault.example.yml', names)

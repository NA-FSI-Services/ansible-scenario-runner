"""Verify expected exit outcomes with real Ansible and an isolated role."""
import json
import shutil
import unittest

import test_runner


@unittest.skipUnless(shutil.which('ansible-playbook'), 'ansible-playbook required')
class ExpectedFailureTests(unittest.TestCase):
    setUp = test_runner.RunnerTests.setUp
    run_script = test_runner.RunnerTests.run_script
    suite = test_runner.RunnerTests.suite

    def prepare(self, tasks=None):
        # This test adapter runs on the host, so retain the local vault URL.
        self.env['API_BASE_URL'] = ''
        role_tasks = self.harness.parent / 'tasks'
        role_tasks.mkdir(exist_ok=True)
        (role_tasks / 'main.yml').write_text(json.dumps(tasks or [
            {'name': 'Reject invalid input', 'ansible.builtin.fail': {'msg': 'invalid input'}}]))
        # Translate navigator arguments to local Ansible to test real role exits.
        nav = self.root / 'bin/ansible-navigator'
        nav.write_text('''#!/usr/bin/env python3
import subprocess, sys
args = sys.argv[3:]
command = ['ansible-playbook', sys.argv[2]]
i = 0
while i < len(args):
    arg = args[i]
    if arg in ('-e', '-i', '--vault-password-file'):
        command.extend(args[i:i+2]); i += 2
    elif arg in ('--eei', '--pp', '--mode', '--pae', '--eev'):
        i += 2
    elif arg.startswith('--co='):
        i += 1
    else:
        command.append(arg); i += 1
sys.exit(subprocess.call(command))
''')
        nav.chmod(0o755)

    def test_role_failure_is_accepted_when_expected(self):
        self.prepare()
        result = self.run_script('run.sh', 'happy-path', EXPECTED_FAILURE='1')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('command failed as expected', result.stderr)

    def test_ordinary_failure_remains_failure(self):
        self.prepare()
        self.assertNotEqual(self.run_script('run.sh', 'happy-path').returncode, 0)

    def test_manifest_boolean_accepts_role_failure(self):
        self.prepare()
        result = self.suite('scenarios: [{name: happy-path, expected_failure: true}]')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('passed=0 failed=1', result.stdout)
        self.assertNotIn('unexpected_outcomes=', result.stdout)

    def test_single_api_check_run_shows_mode_and_skipped_request(self):
        self.prepare()
        source = test_runner.ROOT / 'roles/api_client'
        shutil.copy(source / 'tasks/main.yml', self.harness.parent / 'tasks/main.yml')
        shutil.copytree(source / 'defaults', self.harness.parent / 'defaults')
        (self.harness / 'vault.yml').write_text('{"api_token": "demo-token"}')
        result = self.run_script('run.sh', 'happy-path-check')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('MODE            : CHECK MODE (dry run)', result.stdout)
        self.assertIn('Check mode      : true', result.stdout)
        self.assertIn('No API call was made', result.stdout)
        logs = list((self.root / 'logs').glob('*.log'))
        self.assertEqual(len(logs), 1)
        self.assertIn('MODE            : CHECK MODE (dry run)', logs[0].read_text())

    def prepare_api(self, token='demo-token'):
        import importlib.util
        import threading
        self.prepare()
        source = test_runner.ROOT / 'roles/api_client'
        shutil.copy(source / 'tasks/main.yml', self.harness.parent / 'tasks/main.yml')
        shutil.copytree(source / 'defaults', self.harness.parent / 'defaults')
        spec = importlib.util.spec_from_file_location('expected_failure_mock', source / 'tests/mock_api/server.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.EXPECTED_TOKEN = token
        server = module.ThreadingHTTPServer(('127.0.0.1', 0), module.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        (self.harness / 'vault.yml').write_text(json.dumps({
            'api_token': token,
            'api_base_url': f'http://127.0.0.1:{server.server_port}',
        }))
        return server

    def test_api_suite_with_expected_failure(self):
        self.prepare_api()
        result = self.run_script('run-all.sh')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('passed=6 failed=1', result.stdout)
        self.assertIn('FAIL unknown-path (expected)', result.stdout)
        self.assertIn('PASS unauthorized', result.stdout)
        self.assertIn('PASS not-found', result.stdout)
        self.assertNotIn('unexpected_outcomes=', result.stdout)
        self.assertIn('MODE            : NORMAL MODE (live requests)', result.stdout)
        self.assertIn('MODE            : CHECK MODE (dry run)', result.stdout)
        self.assertIn('No API call was made', result.stdout)

    def test_verbose_requests_hide_credentials_and_preserve_status_diagnostics(self):
        token = 'private-token-regression-sentinel'
        self.prepare_api(token)
        for scenario, rc in (('happy-path', 0), ('unknown-path', 2)):
            with self.subTest(scenario=scenario):
                log = self.root / f'{scenario}.log'
                result = self.run_script('run.sh', scenario, '3', LOG_FILE=str(log))
                output = result.stdout + result.stderr
                self.assertEqual(result.returncode, rc, output)
                self.assertNotIn(token, output)
                self.assertNotIn(token, log.read_text())
                if rc:
                    self.assertIn('HTTP status 404; expected 200', output)
                    self.assertIn('HTTP status 404; expected 200', log.read_text())

    def test_exact_http_status_scenario_rejects_unreachable_api(self):
        import socket
        self.prepare_api()
        # Reserve a local port without listening so the request cannot succeed.
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            result = self.run_script('run.sh', 'not-found',
                                     API_BASE_URL=f'http://127.0.0.1:{sock.getsockname()[1]}')
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('HTTP status -1; expected 404', result.stdout)

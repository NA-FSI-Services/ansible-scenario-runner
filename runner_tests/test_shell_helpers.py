"""Exercise lifecycle, credential permissions, and log allocation in isolation."""
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'roles/api_client/tests'


class HelperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.harness = self.root / 'tests'
        shutil.copytree(SOURCE, self.harness, ignore=shutil.ignore_patterns(
            'vault.yml', '.vault_pass', '*.log', '.mock_api.*', '__pycache__'))
        self.env = dict(os.environ)
        for key in ('MOCK_API_HOST', 'MOCK_API_PORT', 'MOCK_API_TOKEN', 'VAULT_PASS', 'LOGS_DIR'):
            self.env.pop(key, None)
        self.env['MOCK_API_HOST'] = '127.0.0.1'

    def run_script(self, path, *args):
        return subprocess.run(['bash', str(self.harness / path), *args],
                              env=self.env, capture_output=True, text=True, timeout=15)

    def api(self, action):
        return self.run_script('mock_api/manage.sh', action)

    def free_port(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            return str(sock.getsockname()[1])

    def test_api_start_status_restart_stop(self):
        self.env['MOCK_API_PORT'] = self.free_port()
        self.addCleanup(self.api, 'stop')
        for action in ('start', 'status', 'start', 'restart', 'stop', 'stop'):
            with self.subTest(action=action):
                result = self.api(action)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotEqual(self.api('status').returncode, 0)
        self.assertFalse((self.harness / 'mock_api/.mock_api.ready').exists())

    def test_stale_or_unrelated_pid_is_never_signalled(self):
        proc = subprocess.Popen(['sleep', '30'])
        try:
            for value in (str(proc.pid), f'{proc.pid} ' + 'a' * 32, '0 ' + 'a' * 32):
                with self.subTest(value=value):
                    (self.harness / 'mock_api/.mock_api.pid').write_text(value + '\n')
                    self.assertNotEqual(self.api('status').returncode, 0)
                    self.assertEqual(self.api('stop').returncode, 0)
                    self.assertIsNone(proc.poll())
        finally:
            proc.terminate()
            proc.wait()

    def test_occupied_port_fails_and_cleans_process_record(self):
        # A healthy unrelated HTTP service must not satisfy this launcher's readiness.
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            self.env['MOCK_API_PORT'] = str(server.server_port)
            result = self.api('start')
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn('failed to become healthy', result.stderr)
            self.assertFalse((self.harness / 'mock_api/.mock_api.pid').exists())
            self.assertFalse((self.harness / 'mock_api/.mock_api.ready').exists())
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def vault_stub(self, body):
        bins = self.root / 'bin'
        bins.mkdir()
        stub = bins / 'ansible-vault'
        stub.write_text('#!/bin/sh\n' + body)
        stub.chmod(0o755)
        self.env['PATH'] = str(bins) + ':' + self.env['PATH']

    def interactive_vault(self, responses):
        import pty
        import select
        import time
        master, slave = pty.openpty()
        proc = subprocess.Popen(['bash', str(self.harness / 'setup-vault.sh')],
                                env=self.env, stdin=slave, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT)
        os.close(slave)
        output = b''
        try:
            for prompt, answer in responses:
                deadline = time.monotonic() + 5
                while prompt.encode() not in output:
                    self.assertLess(time.monotonic(), deadline, output.decode())
                    if select.select([proc.stdout], [], [], 0.1)[0]:
                        chunk = os.read(proc.stdout.fileno(), 4096)
                        self.assertTrue(chunk, output.decode())
                        output += chunk
                os.write(master, (answer + '\n').encode())
            tail, _ = proc.communicate(timeout=5)
            return proc.returncode, (output + tail).decode()
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            proc.stdout.close()
            os.close(master)

    def test_new_vault_password_is_confirmed_and_not_echoed(self):
        self.vault_stub('exit 0\n')
        rc, output = self.interactive_vault([
            ('New vault password: ', 'test-private-password'),
            ('Confirm vault password: ', 'test-private-password'),
        ])
        self.assertEqual(rc, 0, output)
        self.assertIn('Choose a new password', output)
        self.assertIn('separate from the demo API token', output)
        self.assertNotIn('test-private-password', output)
        self.assertEqual((self.harness / '.vault_pass').read_text(), 'test-private-password\n')
        self.assertEqual((self.harness / '.vault_pass').stat().st_mode & 0o777, 0o600)

    def test_mismatched_confirmation_creates_no_credentials(self):
        self.vault_stub('exit 0\n')
        rc, output = self.interactive_vault([
            ('New vault password: ', 'first'), ('Confirm vault password: ', 'different'),
        ])
        self.assertNotEqual(rc, 0)
        self.assertIn('passwords do not match', output)
        self.assertFalse((self.harness / '.vault_pass').exists())
        self.assertFalse((self.harness / 'vault.yml').exists())

    def test_empty_interactive_password_creates_no_credentials(self):
        self.vault_stub('exit 0\n')
        rc, output = self.interactive_vault([('New vault password: ', '')])
        self.assertNotEqual(rc, 0)
        self.assertIn('empty password not allowed', output)
        self.assertFalse((self.harness / '.vault_pass').exists())
        self.assertFalse((self.harness / 'vault.yml').exists())

    def test_existing_encrypted_vault_asks_for_existing_password(self):
        self.vault_stub('exit 0\n')
        (self.harness / 'vault.yml').write_text('$ANSIBLE_VAULT;1.1;AES256\nfixture\n')
        rc, output = self.interactive_vault([('Existing vault password: ', 'existing')])
        self.assertEqual(rc, 0, output)
        self.assertNotIn('New vault password:', output)
        self.assertNotIn('Confirm vault password:', output)

    @unittest.skipUnless(shutil.which('ansible-vault'), 'ansible-vault required')
    def test_existing_vault_password_is_verified_and_failed_setup_can_be_retried(self):
        vault = self.harness / 'vault.yml'
        password = self.harness / '.vault_pass'
        vault.write_text('api_token: vault-content-sentinel\n')
        password.write_text('correct-password\n')
        result = subprocess.run(['ansible-vault', 'encrypt', str(vault),
                                 '--vault-password-file', str(password)],
                                env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        encrypted = vault.read_bytes()
        password.unlink()

        self.env['VAULT_PASS'] = 'wrong-password'
        result = self.run_script('setup-vault.sh')
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('could not decrypt', result.stderr)
        self.assertFalse(password.exists())
        self.assertEqual(vault.read_bytes(), encrypted)

        # A pre-existing password file belongs to the user; retain it on failure.
        password.write_text('wrong-password\n')
        result = self.run_script('setup-vault.sh')
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(password.read_text(), 'wrong-password\n')
        password.unlink()

        self.env['VAULT_PASS'] = 'correct-password'
        result = self.run_script('setup-vault.sh')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('password verified', result.stdout)
        self.assertNotIn('vault-content-sentinel', result.stdout + result.stderr)
        self.assertNotIn('correct-password', result.stdout + result.stderr)
        self.assertEqual(vault.read_bytes(), encrypted)
        self.assertEqual(password.stat().st_mode & 0o777, 0o600)

    def test_encryption_failure_leaves_only_private_files(self):
        self.vault_stub('exit 1\n')
        result = self.run_script('setup-vault.sh', '--demo')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('may still contain plaintext', result.stderr)
        for filename in ('.vault_pass', 'vault.yml'):
            self.assertEqual((self.harness / filename).stat().st_mode & 0o777, 0o600)

    def test_existing_credentials_are_restricted(self):
        self.vault_stub('exit 1\n')
        for filename in ('.vault_pass', 'vault.yml'):
            path = self.harness / filename
            path.write_text('dummy')
            path.chmod(0o644)
        self.run_script('setup-vault.sh')
        for filename in ('.vault_pass', 'vault.yml'):
            self.assertEqual((self.harness / filename).stat().st_mode & 0o777, 0o600)

    def test_missing_vault_tool_creates_no_credentials(self):
        bins = self.root / 'bin'
        bins.mkdir()
        # The script only needs dirname before its dependency preflight.
        (bins / 'dirname').symlink_to(shutil.which('dirname'))
        result = subprocess.run([shutil.which('bash'), str(self.harness / 'setup-vault.sh'), '--demo'],
                                env=dict(self.env, PATH=str(bins)), capture_output=True, text=True)
        self.assertEqual(result.returncode, 127, result.stderr)
        self.assertFalse((self.harness / '.vault_pass').exists())
        self.assertFalse((self.harness / 'vault.yml').exists())

    def test_vault_symlink_is_rejected(self):
        self.vault_stub('exit 0\n')
        target = self.root / 'untouched'
        target.write_text('keep')
        (self.harness / '.vault_pass').symlink_to(target)
        result = self.run_script('setup-vault.sh', '--demo')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(target.read_text(), 'keep')

    def test_log_names_are_unique_even_with_identical_prefix_and_labels(self):
        result = subprocess.run(['bash', '-c', '''
set -euo pipefail
source "$1/env.sh"
source "$1/lib/log.sh"
scenario_log_path 'a/b' fixed
scenario_log_path 'a_b' fixed
scenario_log_path 'suite-summary' fixed
suite_log_path fixed
suite_log_path fixed
''', 'bash', str(self.harness)], env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        paths = result.stdout.splitlines()
        self.assertEqual([Path(path).name for path in paths], [
            'a_b_fixed.log', 'a_b_fixed_2.log', 'suite-summary_fixed.log',
            'suite-summary_fixed_2.log', 'suite-summary_fixed_3.log',
        ])
        for path in paths:
            self.assertTrue(Path(path).exists())
            self.assertEqual(Path(path).stat().st_mode & 0o777, 0o600)

    def test_concurrent_log_reservations_preserve_existing_content(self):
        from concurrent.futures import ThreadPoolExecutor
        logs = self.harness / 'logs'
        (logs / 'happy-path_fixed.log').write_text('previous run')
        def reserve(_):
            result = subprocess.run(['bash', '-c',
                'source "$1/env.sh"; source "$1/lib/log.sh"; scenario_log_path happy-path fixed',
                'bash', str(self.harness)], env=self.env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            return Path(result.stdout.strip()).name
        with ThreadPoolExecutor(max_workers=4) as pool:
            names = list(pool.map(reserve, range(8)))
        self.assertEqual(set(names), {f'happy-path_fixed_{i}.log' for i in range(2, 10)})
        self.assertEqual((logs / 'happy-path_fixed.log').read_text(), 'previous run')

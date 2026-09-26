import json
import os
from pathlib import Path
import stat
import tempfile
import time
import unittest
from unittest.mock import patch
from abstract_gpt import actions
from abstract_gpt import cli


class ActionsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.fake = self.base / 'codex'
        self.fake.write_text('''#!/usr/bin/env python3
import os, pathlib, sys, time
home = pathlib.Path(os.environ['CODEX_HOME'])
if sys.argv[1:] == ['login', 'status']:
    print('secret-status-output')
    sys.exit(0 if (home / 'authenticated').exists() else 1)
print('https://auth.openai.com/codex/device', flush=True)
print('ABCD-12345', flush=True)
print('secret-refresh-token', flush=True)
time.sleep(0.4)
home.mkdir(exist_ok=True)
(home / 'authenticated').touch()
''')
        self.fake.chmod(0o700)
        self.env = patch.dict(os.environ, {'AG_ROOT': str(self.base / 'data'),
            'CODEX_HOME': str(self.base / 'home'), 'AG_CODEX_BIN': str(self.fake)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_device_login_shared_worker_and_redaction(self):
        self.assertEqual(actions.login_start()['state'], 'pending')
        self.assertEqual(actions.login_start()['state'], 'pending')
        seen_code = False
        for _ in range(100):
            result = actions.login_poll()
            self.assertNotIn('secret', json.dumps(result))
            seen_code |= result.get('user_code') == 'ABCD-12345'
            if result['state'] != 'pending':
                break
            time.sleep(0.03)
        self.assertTrue(seen_code)
        self.assertEqual(result['state'], 'authenticated')
        self.assertTrue(actions.auth_status()['authenticated'])
        self.assertFalse((actions.root() / 'login-output.txt').exists())
        self.assertEqual(stat.S_IMODE(actions.root().stat().st_mode), 0o700)

    def test_user_binary_preferred_over_snap(self):
        local = self.base / '.local/bin/codex'
        local.parent.mkdir(parents=True)
        local.touch()
        with patch.dict(os.environ, {'AG_CODEX_BIN': ''}), \
             patch('pathlib.Path.home', return_value=self.base), \
             patch('shutil.which', return_value='/snap/bin/codex'):
            self.assertEqual(actions.binary(), str(local))

    def test_dead_worker(self):
        actions.init_root()
        actions.write(actions.root() / 'login.json', '{"state":"pending"}')
        self.assertEqual(actions.login_poll()['state'], 'interrupted')

    def test_template_preserves_auth_and_live_config(self):
        home = actions.codex_home()
        home.mkdir()
        (home / 'auth.json').write_text('secret')
        (home / 'config.toml').write_text('model = "example"\n')
        actions.save_template()
        self.assertFalse((actions.root() / 'template/auth.json').exists())
        (home / 'config.toml').write_text('model = "changed"\n')
        self.assertFalse(actions.restore()['changed'])
        (home / 'config.toml').unlink()
        self.assertTrue(actions.restore()['changed'])
        self.assertEqual((home / 'auth.json').read_text(), 'secret')

    def test_launch_flags_and_status_redaction(self):
        actions.set_model('configured')
        with patch('abstract_gpt.comms.ensure_hook'), patch('subprocess.call', return_value=7) as call:
            self.assertEqual(actions.launch(['--model', 'explicit', 'hello'], True), 7)
            self.assertEqual(call.call_args.args[0],
                [str(self.fake), *actions.toolserver_args(), '--dangerously-bypass-hook-trust',
                 'exec', '--ephemeral', '--model', 'explicit', 'hello'])
            actions.launch(['hello'])
            self.assertEqual(call.call_args.args[0], [str(self.fake), *actions.toolserver_args(),
                '--dangerously-bypass-hook-trust', '--model', 'configured', 'hello'])
        self.assertNotIn('secret', json.dumps(actions.auth_status()))

    def test_comms_can_be_disabled(self):
        actions.set_comms(False)
        with patch('subprocess.call', return_value=0) as call:
            actions.launch(['hello'])
        self.assertNotIn('--dangerously-bypass-hook-trust', call.call_args.args[0])

    def test_cli_launch_forwards_yolo_without_separator(self):
        argv = ['abstract-gpt', 'launch', '--yolo', 'resume', 'session-id']
        with patch('sys.argv', argv), patch.object(cli.actions, 'launch', return_value=0) as launch:
            self.assertEqual(cli.main(), 0)
        launch.assert_called_once_with(['--yolo', 'resume', 'session-id'], execute=False)


if __name__ == '__main__':
    unittest.main()

import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from abstract_gpt import comms


class CommsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            'AG_ROOT': str(Path(self.tmp.name) / 'data'),
            'CODEX_HOME': str(Path(self.tmp.name) / 'codex'),
        })
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_hook_install_is_idempotent_and_preserves_other_hooks(self):
        home = Path(os.environ['CODEX_HOME'])
        home.mkdir()
        (home / 'hooks.json').write_text(json.dumps({
            'hooks': {'SessionStart': [{'matcher': 'compact', 'hooks': [
                {'type': 'command', 'command': 'keep-me'}]}]}}))
        comms.ensure_hook()
        comms.ensure_hook()
        doc = json.loads((home / 'hooks.json').read_text())
        commands = [h['command'] for g in doc['hooks']['SessionStart'] for h in g['hooks']]
        self.assertIn('keep-me', commands)
        self.assertEqual(sum(comms.HOOK_MARKER in c for c in commands), 1)

    def test_session_hook_opens_binds_and_reuses_channel(self):
        calls = []
        def fake(name, args):
            calls.append((name, args))
            if name == 'channel_open':
                return {'channel_id': 'ch_test1234', 'url': 'https://example/ch/test?t=x',
                        'expires_at': 0}
            return {'bound': True}
        event = io.StringIO(json.dumps({'session_id': 'thread-1', 'cwd': '/work'}))
        with patch.object(comms, '_mcp_call', side_effect=fake), patch('sys.stdout', new=io.StringIO()) as out:
            self.assertEqual(comms.session_start_hook(event), 0)
            payload = json.loads(out.getvalue())
        self.assertIn('https://example/ch/test?t=x', payload['systemMessage'])
        self.assertEqual([x[0] for x in calls], ['channel_open', 'channel_bind'])
        calls.clear()
        with patch.object(comms, '_mcp_call', side_effect=fake):
            state = comms.bind_session('thread-1', '/work')
        self.assertEqual(state['channel_id'], 'ch_test1234')
        self.assertEqual([x[0] for x in calls], ['channel_bind'])


if __name__ == '__main__':
    unittest.main()

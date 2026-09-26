import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from abstract_gpt import mct_repl as mct


class MctTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name)
        self.exchange = self.ws / 'exchange'
        self.exchange.mkdir()
        self.fake = self.ws / 'codex'
        self.fake.write_text('''#!/usr/bin/env python3
import json, pathlib, re, sys, time
args = sys.argv[1:]
with open('commands.jsonl', 'a') as f:
    f.write(json.dumps(args) + '\\n')
pointer = args[-1]
assert 'operator-guidance-marker' in ' '.join(args)
prompt = pathlib.Path(re.search(r'Read your prompt from: (.+)', pointer)[1])
response = pathlib.Path(re.search(r'Write your complete answer \\(markdown\\) to: (.+)', pointer)[1])
print(json.dumps({'type': 'thread.started', 'thread_id': 'test-thread'}), flush=True)
time.sleep(.1)
response.write_text('Answer: ' + prompt.read_text())
print(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'DONE'}}), flush=True)
''')
        self.fake.chmod(0o700)
        (self.ws / 'operator-guidance.md').write_text('operator-guidance-marker')
        self.environment = patch.dict(os.environ, {'AG_CODEX_BIN': str(self.fake),
                                                   'AG_ROOT': str(self.ws / 'config')})
        self.environment.start()
        self.archive = patch.object(mct.Archive, 'start')
        self.archive.start()

    def tearDown(self):
        self.archive.stop()
        self.environment.stop()
        self.tmp.cleanup()

    def wait(self, arb, turns):
        deadline = time.monotonic() + 5
        idle_since = None
        while time.monotonic() < deadline:
            responses = len(list(self.exchange.glob('turn-*-response.md')))
            archived = len(list((self.ws / 'archive').glob('pending-*.json')))
            if arb.busy() or responses < turns or archived < turns:
                idle_since = None
            elif idle_since is None:
                idle_since = time.monotonic()
            elif time.monotonic() - idle_since >= .2:
                break
            time.sleep(.02)
        self.assertFalse(arb.busy())

    def test_pointer_turn_resume_and_reset(self):
        arb = mct.Arbiter(self.ws, self.exchange, 'test-model', 10, fresh=True)
        arb.tui = True
        with contextlib.redirect_stdout(io.StringIO()):
            arb.dispatch('first question')
            self.wait(arb, 1)
            self.assertEqual(arb.sid, 'test-thread')
            arb.dispatch('second question')
            self.wait(arb, 2)
            commands = [json.loads(line) for line in (self.ws / 'commands.jsonl').read_text().splitlines()]
            self.assertNotIn('resume', commands[0])
            self.assertIn('resume', commands[1])
            self.assertIn('test-thread', commands[1])
            self.assertNotIn('first question', commands[0][-1])
            self.assertIn('workspace-write', commands[0])
            self.assertNotIn('--ephemeral', commands[0])
            self.assertEqual((self.exchange / 'turn-0002-response.md').read_text(), 'Answer: second question\n')
            self.assertEqual(arb.reset_session(), 'test-thread')
            self.assertFalse((self.ws / 'session.json').exists())
            self.assertTrue(list((self.ws / 'archive').glob('pending-*.json')))

    def test_arbitration_preserves_capture_coalesce_and_append(self):
        arb = mct.Arbiter(self.ws, self.exchange, '', 10, fresh=True)
        arb.tui = True
        with contextlib.redirect_stdout(io.StringIO()):
            arb.dispatch('first')
            arb.dispatch('second')
            arb.dispatch('+third')
            arb.dispatch('todo: later')
            self.wait(arb, 3)
        prompts = sorted(self.exchange.glob('*-prompt.md'))
        self.assertEqual(len(prompts), 3)
        self.assertIn('[B digest', prompts[1].read_text())
        self.assertIn('second', prompts[1].read_text())
        self.assertEqual(prompts[2].read_text(), 'third\n')
        self.assertIn('later', (self.ws / 'todo.md').read_text())

    def test_launch_fresh_default_and_explicit_resume(self):
        with patch.object(mct, '_run_plain', return_value=0) as run, \
             contextlib.redirect_stdout(io.StringIO()):
            (self.ws / 'session.json').write_text('{"session_id":"previous"}')
            mct.main([str(self.ws), '--plain', '--resume', '--dangerous'])
            self.assertEqual(run.call_args.args[0].sid, 'previous')
            self.assertTrue(run.call_args.args[0].dangerous)
            mct.main([str(self.ws), '--plain'])
            self.assertIsNone(run.call_args.args[0].sid)
            self.assertFalse(run.call_args.args[0].dangerous)

    def test_dangerous_uses_full_codex_bypass_on_resumed_turn(self):
        (self.ws / 'session.json').write_text('{"session_id":"previous"}')
        arb = mct.Arbiter(self.ws, self.exchange, '', 10,
                          dangerous=True)
        arb.tui = True
        with contextlib.redirect_stdout(io.StringIO()):
            arb.dispatch('dangerous question')
            self.wait(arb, 1)
        command = json.loads((self.ws / 'commands.jsonl').read_text().splitlines()[0])
        self.assertIn('--dangerously-bypass-approvals-and-sandbox', command)
        self.assertNotIn('--sandbox', command)
        self.assertNotIn('approval_policy="never"', command)
        self.assertIn('resume', command)
        self.assertIn('previous', command)


if __name__ == '__main__':
    unittest.main()

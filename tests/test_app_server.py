import json
from pathlib import Path
import queue
import sys
import tempfile
import threading
import unittest

from abstract_gpt.app_server import Adapter, RpcClient


class FakeClient:
    def __init__(self, **kwargs):
        self.calls = []
        self.sent = []
        self.events = queue.Queue()
        self.closed = False

    def call(self, method, params):
        self.calls.append((method, params))
        if method in ('thread/start', 'thread/resume'):
            return {'thread': {'id': 'native-gpt'}, 'model': 'test'}
        if method == 'turn/start':
            self.events.put({'id': 'approval-9', 'method': 'item/commandExecution/requestApproval',
                             'params': {'threadId': 'native-gpt', 'command': 'touch file'}})
            return {'turn': {'id': 'turn-1'}}
        if method == 'turn/interrupt':
            self.events.put({'method': 'turn/completed', 'params': {'threadId': 'native-gpt',
                             'turn': {'id': 'turn-1', 'status': 'interrupted'}}})
            return {}
        raise AssertionError(method)

    def send(self, obj):
        self.sent.append(obj)
        self.events.put({'method': 'item/agentMessage/delta', 'params': {'threadId': 'native-gpt', 'delta': 'answer'}})
        self.events.put({'method': 'item/completed', 'params': {'threadId': 'native-gpt',
                         'item': {'type': 'agentMessage', 'id': 'a1', 'text': 'answer'}}})
        self.events.put({'method': 'turn/completed', 'params': {'threadId': 'native-gpt',
                         'turn': {'id': 'turn-1', 'status': 'completed'}}})

    def close(self):
        self.closed = True


class AdapterTests(unittest.TestCase):
    def test_resume_permissions_and_request_id_roundtrip(self):
        client = FakeClient()
        adapter = Adapter(lambda **kw: client)
        events = []
        def emit(e):
            events.append(e)
            if e['type'] == 'approval':
                adapter.respond(e['request_id'], 'accept')
        result = adapter.run({'cwd': '/tmp', 'native_id': 'native-gpt', 'effort': 'high', 'model': 'test'}, 'do work', emit)
        self.assertEqual(result, 'answer')
        method, params = client.calls[0]
        self.assertEqual(method, 'thread/resume')
        self.assertEqual(params['threadId'], 'native-gpt')
        self.assertEqual(params['sandbox'], 'workspace-write')
        self.assertEqual(params['approvalPolicy'], 'on-request')
        self.assertEqual(client.calls[1][1]['effort'], 'high')
        self.assertEqual(client.sent[0], {'id': 'approval-9', 'result': {'decision': 'accept'}})
        self.assertTrue(client.closed)
        with self.assertRaises(ValueError):
            adapter.respond('approval-9', 'accept')

    def test_unrestricted_is_explicit_and_interrupt_is_native(self):
        client = FakeClient()
        adapter = Adapter(lambda **kw: client)
        def emit(e):
            if e['type'] == 'approval': adapter.interrupt()
        with self.assertRaisesRegex(RuntimeError, 'interrupted'):
            adapter.run({'cwd': '/tmp', 'dangerous': True}, 'work', emit)
        self.assertEqual(client.calls[0][1]['sandbox'], 'danger-full-access')
        self.assertEqual(client.calls[0][1]['approvalPolicy'], 'never')
        self.assertEqual(client.calls[-1], ('turn/interrupt', {'threadId': 'native-gpt', 'turnId': 'turn-1'}))

    def test_rpc_subprocess_handles_notifications_errors_and_eof(self):
        with tempfile.TemporaryDirectory() as root:
            script = Path(root) / 'rpc.py'
            script.write_text('''import sys,json
for line in sys.stdin:
 q=json.loads(line)
 if 'id' not in q: continue
 if q['method']=='crash': sys.exit(1)
 print(json.dumps({'method':'notice','params':{}}),flush=True)
 r={'error':{'message':'bad request'}} if q['method']=='bad' else {'result':{'ok':True}}
 print(json.dumps(dict(r,id=q['id'])),flush=True)
''')
            c = RpcClient([sys.executable, str(script)], timeout=2)
            try:
                self.assertEqual(c.events.get(timeout=1)['method'], 'notice')
                self.assertEqual(c.call('valid', {}), {'ok': True})
                with self.assertRaisesRegex(RuntimeError, 'bad request'): c.call('bad', {})
                with self.assertRaisesRegex(RuntimeError, 'disconnected'): c.call('crash', {})
            finally:
                c.close()


if __name__ == '__main__':
    unittest.main()


class ScriptedClient:
    """turn/start queues a codex MCP elicitation plus one huge command; the turn
    completes once the elicitation is answered (or immediately when auto-accepted)."""
    def __init__(self, **kwargs):
        self.calls, self.sent, self.events = [], [], queue.Queue()

    def call(self, method, params):
        self.calls.append((method, params))
        if method in ('thread/start', 'thread/resume'):
            return {'thread': {'id': 't'}, 'model': 'test'}
        if method == 'turn/start':
            self.events.put({'id': 7, 'method': 'mcpServer/elicitation/request',
                             'params': {'threadId': 't', 'serverName': 'toolserver', 'mode': 'form',
                                        'message': 'Allow comms_inbox?', 'requestedSchema': {'type': 'object', 'properties': {}}}})
            for _ in range(10):
                self.events.put({'method': 'item/commandExecution/outputDelta',
                                 'params': {'threadId': 't', 'itemId': 'c1', 'delta': 'x' * 20000}})
            self.events.put({'method': 'item/completed', 'params': {'threadId': 't', 'item': {
                'type': 'commandExecution', 'id': 'c1', 'command': 'rg .', 'aggregatedOutput': 'x' * 200000}}})
            return {'turn': {'id': 'turn-1'}}
        raise AssertionError(method)

    def send(self, obj):
        self.sent.append(obj)
        self.events.put({'method': 'turn/completed', 'params': {'threadId': 't', 'turn': {'id': 'turn-1', 'status': 'completed'}}})

    def close(self):
        pass


class ElicitationAndOutputCapTests(unittest.TestCase):
    def run_turn(self, decision=None, dangerous=False):
        from abstract_gpt.app_server import OUTPUT_CAP
        client, events = ScriptedClient(), []
        adapter = Adapter(lambda **kw: client)
        def emit(e):
            events.append(e)
            if e['type'] == 'approval':
                adapter.respond(e['request_id'], decision)
        adapter.run({'cwd': '/tmp', 'dangerous': dangerous}, 'check inbox', emit)
        return client, events, OUTPUT_CAP

    def test_elicitation_is_an_approval_and_accept_maps_to_mcp_action(self):
        client, events, _ = self.run_turn('accept')
        self.assertEqual([e['method'] for e in events if e['type'] == 'approval'], ['mcpServer/elicitation/request'])
        self.assertEqual(client.sent, [{'id': 7, 'result': {'action': 'accept', 'content': {}}}])
        self.assertFalse([e for e in events if e['type'] == 'note' and 'Unsupported' in e.get('text', '')])

    def test_decline_maps_to_decline(self):
        client, _, _ = self.run_turn('decline')
        self.assertEqual(client.sent[0]['result'], {'action': 'decline', 'content': None})

    def test_unrestricted_session_auto_accepts_without_asking(self):
        client, events, _ = self.run_turn(dangerous=True)
        self.assertFalse([e for e in events if e['type'] == 'approval'])
        self.assertEqual(client.sent, [{'id': 7, 'result': {'action': 'accept', 'content': {}}}])

    def test_command_output_is_capped_in_the_console(self):
        _, events, cap = self.run_turn('accept')
        kept = ''.join(e['text'] for e in events if e['type'] == 'tool_result')
        self.assertLess(len(kept), cap + 200)
        self.assertIn('console kept %d of 200000 chars' % cap, kept)
        done = [e for e in events if e['type'] == 'tool' and e['status'] == 'completed'][0]
        self.assertLess(len(done['item']['aggregatedOutput']), cap + 100)

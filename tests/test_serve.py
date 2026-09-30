import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from abstract_claude.console_service import Coordinator
from abstract_gpt.serve import handler


class Adapter:
    def run(self, session, prompt, emit):
        assert session['backend'] == 'gpt'
        return 'GPT received: ' + prompt


class ServeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.service = Coordinator(Path(self.tmp.name) / 'test.db',
                                   compiler=lambda f: ('\n'.join(f), 'direct'),
                                   factory=lambda b: Adapter())
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler(self.service, self.tmp.name))
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.base = 'http://127.0.0.1:' + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.service.close()
        self.tmp.cleanup()

    def request(self, path, body=None, headers=None):
        req = Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                      headers=headers or {})
        with urlopen(req, timeout=3) as response:
            return response.read()

    def test_standalone_ui_and_gpt_conversation(self):
        self.assertIn(b'Abstract GPT Serve', self.request('/'))
        s = json.loads(self.request('/api/console/sessions', {}))
        self.assertEqual(s['backend'], 'gpt')
        self.request('/api/console/chat', {'session_id': s['id'], 'prompt': 'hello'})
        end = time.monotonic() + 3
        while self.service.get(s['id'])['busy'] and time.monotonic() < end:
            time.sleep(.01)
        data = json.loads(self.request('/api/console/events?id=' + s['id']))
        self.assertTrue(any(e.get('result') == 'GPT received: hello' for e in data['events']))
        self.assertEqual(json.loads(self.request('/api/state'))['backends'], ['gpt'])

    def test_cross_origin_and_other_backend_rejected(self):
        with self.assertRaises(HTTPError) as error:
            self.request('/api/console/sessions', {}, {'Origin': 'https://example.com'})
        self.assertEqual(error.exception.code, 401)
        with self.assertRaises(HTTPError) as error:
            self.request('/api/console/sessions', {'backend': 'claude'})
        self.assertEqual(error.exception.code, 400)

    def test_switch_model_preserves_conversation_and_history(self):
        s = json.loads(self.request('/api/console/sessions', {'model': 'first'}))
        self.service.emit(s['id'], {'type': 'done', 'result': 'remembered marker'})
        changed = json.loads(self.request('/api/console/switch', {
            'session_id': s['id'], 'backend': 'gpt', 'model': 'second'}))
        self.assertEqual(changed['id'], s['id'])
        self.assertEqual(changed['model'], 'second')
        self.assertIn('remembered marker', changed['handoff_context'])
        with self.assertRaises(HTTPError):
            self.request('/api/console/switch', {'session_id': s['id'], 'backend': 'hugpy'})

    def test_model_catalog_is_served(self):
        from unittest.mock import patch
        with patch('abstract_gpt.models.catalog', return_value={'models': [
                {'backend': 'gpt', 'model': 'test', 'label': 'GPT test'}], 'error': ''}):
            doc = json.loads(self.request('/api/console/models'))
            self.assertEqual(doc['models'][0]['model'], 'test')

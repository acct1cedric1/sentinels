import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock

import server


class ServerBehaviorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.httpd.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.thread.join(timeout=2)
        cls.httpd.server_close()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            resp = conn.getresponse()
            data = resp.read()
            return resp.status, dict(resp.getheaders()), data
        finally:
            conn.close()

    def test_health_keeps_existing_fields_and_adds_warmup_state(self):
        status, headers, data = self.request("GET", "/api/health")

        self.assertEqual(status, 200)
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
        payload = json.loads(data.decode("utf-8"))
        for key in ("ok", "key", "http", "warming", "base_cached", "last_warmup_error"):
            self.assertIn(key, payload)

    def test_oversized_json_post_is_rejected(self):
        body = b'{"text":"' + (b"x" * (server.MAX_BODY_BYTES + 1)) + b'"}'
        status, _, data = self.request(
            "POST",
            "/api/auth/verify",
            body=body,
            headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
        )

        self.assertEqual(status, 413)
        self.assertEqual(json.loads(data.decode("utf-8")), {"error": "request_too_large"})

    def test_bad_json_post_is_rejected(self):
        body = b'{"text":'
        status, _, data = self.request(
            "POST",
            "/api/auth/verify",
            body=body,
            headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
        )

        self.assertEqual(status, 400)
        self.assertEqual(json.loads(data.decode("utf-8")), {"error": "bad_json"})

    def test_static_traversal_is_rejected(self):
        status, headers, data = self.request("GET", "/../server.py")

        self.assertEqual(status, 404)
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(json.loads(data.decode("utf-8")), {"error": "not found"})

    def test_main_binds_server_before_starting_warmup(self):
        events = []

        class FakeHTTPServer:
            def __init__(self, address, handler):
                events.append("bind")

            def serve_forever(self):
                events.append("serve")
                raise KeyboardInterrupt()

            def shutdown(self):
                events.append("shutdown")

        class FakeThread:
            def __init__(self, target, daemon=False):
                events.append(("thread_init", daemon, target))

            def start(self):
                events.append("thread_start")

        with mock.patch.object(server, "ThreadingHTTPServer", FakeHTTPServer), \
                mock.patch.object(server.helius, "has_key", return_value=False), \
                mock.patch.object(server.threading, "Thread", FakeThread), \
                mock.patch("builtins.print"):
            server.main()

        self.assertEqual(events[0], "bind")
        self.assertIn(("thread_init", True, server._warmup_background), events)
        self.assertLess(events.index("bind"), events.index("thread_start"))


if __name__ == "__main__":
    unittest.main()

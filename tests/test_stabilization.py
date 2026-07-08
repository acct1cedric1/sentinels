import json
import os
import socket
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import auth
import server


ADMIN = "11111111111111111111111111111111"


class ConfigNormalizationTests(unittest.TestCase):
    def setUp(self):
        self.old_config = auth.CONFIG
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        auth.CONFIG = self.path

    def tearDown(self):
        auth.CONFIG = self.old_config
        try:
            os.remove(self.path)
        except OSError:
            pass

    def write_config(self, gating):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"gating": gating}, f)

    def test_gating_accepts_numeric_strings_and_valid_admin_list(self):
        self.write_config({
            "enabled": "false",
            "lock_winrate": "55",
            "min_usd": "7.5",
            "pumpfun_mcap_target": "250000",
            "trading_unlocked": "yes",
            "token_mint": " SOL ",
            "admin_wallets": [f" {ADMIN} ", "not base58!", 123],
        })

        g = auth.gating()

        self.assertFalse(g["enabled"])
        self.assertEqual(g["lock_winrate"], 55)
        self.assertEqual(g["min_usd"], 7.5)
        self.assertEqual(g["pumpfun_mcap_target"], 250000)
        self.assertTrue(g["trading_unlocked"])
        self.assertEqual(g["token_mint"], "SOL")
        self.assertEqual(g["admin_wallets"], {ADMIN})

    def test_gating_does_not_split_admin_wallet_string(self):
        self.write_config({"admin_wallets": ADMIN, "lock_winrate": "bad", "min_usd": -1})

        g = auth.gating()

        self.assertEqual(g["admin_wallets"], set())
        self.assertEqual(g["lock_winrate"], auth._DEFAULT_GATING["lock_winrate"])
        self.assertEqual(g["min_usd"], auth._DEFAULT_GATING["min_usd"])


class ServerStabilizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old_key = server.helius.API_KEY
        cls.old_max = server.MAX_BODY_BYTES
        server.helius.API_KEY = ""
        cls.port = cls.free_port()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", cls.port), server.Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)
        server.helius.API_KEY = cls.old_key
        server.MAX_BODY_BYTES = cls.old_max

    @staticmethod
    def free_port():
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    @classmethod
    def url(cls, path):
        return f"http://127.0.0.1:{cls.port}{path}"

    def get_json(self, path):
        with urllib.request.urlopen(self.url(path), timeout=5) as r:
            return r.status, json.loads(r.read().decode("utf-8")), r.headers

    def post_json_error(self, path, body):
        req = urllib.request.Request(
            self.url(path),
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=5)
        return cm.exception.code, json.loads(cm.exception.read().decode("utf-8"))

    def test_health_has_additive_warmup_fields(self):
        status, body, headers = self.get_json("/api/health")

        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertIn("warming", body)
        self.assertIn("base_cached", body)
        self.assertIn("last_warmup_error", body)
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")

    def test_smartmoney_no_key_still_returns_json_error(self):
        status, body, _ = self.get_json("/api/smartmoney?tf=24h&memecoins=0")

        self.assertEqual(status, 200)
        self.assertEqual(body["error"], "no_api_key")
        self.assertEqual(body["tokens"], [])

    def test_malformed_json_returns_bad_json(self):
        code, body = self.post_json_error("/api/auth/verify", b"{")

        self.assertEqual(code, 400)
        self.assertEqual(body["error"], "bad_json")

    def test_oversized_json_returns_request_too_large(self):
        server.MAX_BODY_BYTES = 4
        try:
            code, body = self.post_json_error("/api/auth/verify", b'{"nonce": ""}')
        finally:
            server.MAX_BODY_BYTES = self.old_max

        self.assertEqual(code, 413)
        self.assertEqual(body["error"], "request_too_large")


if __name__ == "__main__":
    unittest.main()

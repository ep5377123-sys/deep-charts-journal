import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from journal.sheets import SheetsClient, SheetsError


class FakeAppsScript(BaseHTTPRequestHandler):
    """Mimics Google: POST /exec runs the script and answers 302 -> GET /echo serves the result."""
    last_body = None

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeAppsScript.last_body = body
        if self.path == "/html":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<html>Sign in</html>")
            return
        ok = body.get("token") == "secret"
        self.server.result = json.dumps({"ok": ok, **({} if ok else {"error": "bad token"})}).encode()
        self.send_response(302)
        self.send_header("Location", "/echo")
        self.end_headers()

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(self.server.result)

    def log_message(self, *args):
        pass


class SheetsClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), FakeAppsScript)
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def client(self, path="/exec", token="secret"):
        return SheetsClient(self.base + path, token, "PC1", "0.1.0", timeout=5)

    def test_post_follows_redirect_and_sends_identity(self):
        self.assertEqual(self.client().post({"ping": True}), {"ok": True})
        self.assertEqual(FakeAppsScript.last_body, {"ping": True, "token": "secret", "pc": "PC1", "version": "0.1.0"})

    def test_script_error_raises(self):
        with self.assertRaisesRegex(SheetsError, "bad token"):
            self.client(token="wrong").post({})

    def test_html_response_raises(self):
        with self.assertRaisesRegex(SheetsError, "non-JSON"):
            self.client("/html").post({})

    def test_unreachable_raises(self):
        with self.assertRaisesRegex(SheetsError, "network"):
            SheetsClient("http://127.0.0.1:9/exec", "t", "PC1", "0", timeout=2).post({})

    def test_missing_config_raises(self):
        with self.assertRaises(SheetsError):
            SheetsClient("", "", "PC1", "0")


if __name__ == "__main__":
    unittest.main()

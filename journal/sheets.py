"""Client for the Apps Script web app (apps_script/Code.gs) bound to the journal Sheet."""

import json
import urllib.error
import urllib.request


class SheetsError(Exception):
    pass


class SheetsClient:
    def __init__(self, url, token, pc, version, timeout=90):
        if not url or not token:
            raise SheetsError("webapp_url and token must be set in config.json (run: python -m journal init)")
        self.url, self.token, self.pc, self.version, self.timeout = url, token, pc, version, timeout

    def post(self, body):
        data = json.dumps({**body, "token": self.token, "pc": self.pc, "version": self.version}).encode("utf-8")
        req = urllib.request.Request(self.url, data=data, headers={"Content-Type": "application/json"})
        # Apps Script answers a POST with a 302 to googleusercontent.com; urllib follows it as a GET.
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                text = r.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            raise SheetsError(f"HTTP {e.code}: {e.read()[:300]!r}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise SheetsError(f"network: {e}") from e
        try:
            resp = json.loads(text)
        except ValueError:
            raise SheetsError(f"non-JSON response (is the web app deployed with access 'Anyone'?): {text[:300]}")
        if not resp.get("ok"):
            raise SheetsError(f"web app error: {resp.get('error')}")
        return resp

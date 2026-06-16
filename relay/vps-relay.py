#!/usr/bin/env python3
"""Polymarket read-only relay — tiny stdlib HTTP server for an overseas VPS.

Run this on a server that CAN reach Polymarket and is reachable from China
(e.g. a Hong Kong / Singapore / Japan VPS, Tencent Lighthouse 国际版, etc.).
Mainland users then point the skill's config at this relay.

Routes:
  GET /gamma/<path>?<query>  ->  https://gamma-api.polymarket.com/<path>?<query>
  GET /clob/<path>?<query>   ->  https://clob.polymarket.com/<path>?<query>
  GET /healthz               ->  "ok"

Run:
  python3 vps-relay.py 0.0.0.0 8787
  # behind nginx/caddy with TLS is recommended; or use `--key` for light auth.

Optional shared-secret auth:
  python3 vps-relay.py 0.0.0.0 8787 --key MYSECRET
  then callers must send header  X-Relay-Key: MYSECRET
  (the fetch script doesn't send this by default; prefer TLS + firewall, or
   add the header via a reverse proxy. The key here is a simple extra gate.)

Then in scripts/polymarket.config.json:
  "proxy": "direct",
  "gamma_base": "http://YOUR_VPS:8787/gamma",
  "clob_base":  "http://YOUR_VPS:8787/clob"
"""

import sys
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TARGETS = {
    "/gamma": "https://gamma-api.polymarket.com",
    "/clob": "https://clob.polymarket.com",
}
REQUIRED_KEY = None  # set via --key
TIMEOUT = 25


class Handler(BaseHTTPRequestHandler):
    server_version = "yucedi-relay/1.0"

    def _send(self, status, body, ctype="text/plain; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/healthz":
            return self._send(200, "ok")
        if REQUIRED_KEY and self.headers.get("X-Relay-Key") != REQUIRED_KEY:
            return self._send(401, "unauthorized")

        target = None
        for prefix, base in TARGETS.items():
            if self.path == prefix or self.path.startswith(prefix + "/"):
                target = base + self.path[len(prefix):]
                break
        if not target:
            return self._send(404, "not found (use /gamma/... or /clob/...)")

        try:
            req = urllib.request.Request(
                target, headers={"User-Agent": "yucedi-relay", "Accept": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                data = resp.read()
                ctype = resp.headers.get("Content-Type", "application/json")
            self._send(200, data, ctype)
        except Exception as exc:  # noqa: BLE001
            self._send(502, f"relay upstream error: {exc}")

    def log_message(self, *_args):  # quiet
        pass


def main():
    host = sys.argv[1] if len(sys.argv) > 1 else "0.0.0.0"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8787
    global REQUIRED_KEY
    if "--key" in sys.argv:
        REQUIRED_KEY = sys.argv[sys.argv.index("--key") + 1]
    print(f"yucedi relay listening on {host}:{port} (auth={'on' if REQUIRED_KEY else 'off'})")
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()

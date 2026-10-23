#!/usr/bin/env python3
"""A throwaway stand-in for the Core, so the test bench runs with nothing but this repo.

It answers just enough of the WebTrit API for the signup-as-login bench:

    POST /api/v1/session   -> 200 {token, user_id, tenant_id}      a normal login
    POST /tenant/<id>/api/v1/session -> same, echoing the tenant
    POST /api/v1/user      -> 422                                   signup is NOT served here
    GET  /api/v1/user      -> 200 UserInfo  with a valid Bearer     the live session's profile
                           -> 401           without one

So, pointed straight at this fake Core, a custom signup fails (422) - that is the stand with no
custom-signup backend. With the proxy's --signup-as-login in front, the app's POST /user is turned
into POST /session and gets the token below. The client then enters the session with that token:
GET /api/v1/user with the Bearer returns the profile, so the login is real, not just a token echo.
CORS is open so the browser client can read the replies.
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def _cors(self) -> None:
        self.send_header("access-control-allow-origin", "*")
        self.send_header("access-control-allow-methods", "POST, GET, OPTIONS")
        self.send_header("access-control-allow-headers", "content-type, authorization")

    def _send(self, status: int, body: dict) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self._cors()
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_OPTIONS(self) -> None:  # CORS preflight
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _bearer_login(self) -> str | None:
        """The login baked into the token this fake Core hands out (`fake-token-for-<login>`)."""
        auth = self.headers.get("authorization", "")
        prefix = "Bearer fake-token-for-"
        return auth[len(prefix):] if auth.startswith(prefix) else None

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0].rstrip("/")
        if path.startswith("/tenant/"):
            path = "/" + path.split("/", 3)[3] if len(path.split("/", 3)) > 3 else path
        if path.endswith("/api/v1/system-info"):
            # Minimal valid SystemInfo: the app validates the backend here right after login.
            self.log("GET /system-info -> 200")
            self._send(200, {
                "core": {"version": "1.0.0", "ice_servers_configured": True},
                "postgres": {"version": "16.0"},
            })
        elif path.endswith("/health-check"):
            self._send(200, {"status": "ok"})
        elif path.endswith("/api/v1/user"):
            login = self._bearer_login()
            if login is None:
                self.log("GET /user without a valid token -> 401")
                self._send(401, {"code": "authorization_header_missing"})
                return
            self.log(f"session entered: GET /user as {login!r} -> 200")
            self._send(200, {
                "email": f"{login}@bench.local",
                "first_name": login.capitalize(),
                "last_name": "Bench",
                "company_name": "WebTrit Bench",
                "numbers": {"main": "101", "ext": "101", "additional": []},
                "balance": {"amount": 0.0, "currency": "USD", "balance_type": "unknown"},
            })
        else:
            self._send(404, {"code": "not_found", "path": self.path})

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            body = {}
        path = self.path.split("?", 1)[0].rstrip("/")

        tenant_id = ""
        if path.startswith("/tenant/"):
            tenant_id = path.split("/", 3)[2]
            path = "/" + path.split("/", 3)[3] if len(path.split("/", 3)) > 3 else path

        if path.endswith("/api/v1/session"):
            # A normal login: hand back a token for whoever logged in.
            self.log("login as %r (tenant %r)" % (body.get("login"), tenant_id or None))
            self._send(200, {
                "token": "fake-token-for-" + str(body.get("login", "unknown")),
                "user_id": "user-" + str(body.get("login", "unknown")),
                "tenant_id": tenant_id or body.get("tenant_id") or "",
            })
        elif path.endswith("/api/v1/user"):
            # Custom signup is not served on this stand.
            self.log("signup refused (no custom-signup backend on this stand)")
            self._send(422, {"code": "signup_not_supported", "details": "no custom-signup backend"})
        else:
            self._send(404, {"code": "not_found", "path": self.path})

    def log(self, msg: str) -> None:
        print("[fake-core] " + msg, flush=True)

    def log_message(self, *args) -> None:  # silence the default access log
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9100)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[fake-core] listening on http://{args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()

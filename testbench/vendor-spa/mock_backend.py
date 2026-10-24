#!/usr/bin/env python3
"""A local stand-in for a vendor's embedded login page and its backend.

The page in `site/` is a production vendor single-page app (React, one bundle) copied as it was
and rebranded to "Demo Vendor". It runs inside the PortaPhone app's WebView as the launch login
page and talks to two parties:

  - its own backend, same origin, under /api/v1 (phone number -> SMS code -> PortaBilling tokens);
  - the app, over the WebtritLoginChannel: {event: "signup", data: {access_token, refresh_token},
    callback?} once the code is verified.

This server plays the backend, so the page can be run against a stand without touching the
vendor's production. One origin serves both: the page reads its API from
`window.location.origin + "/api/v1"`.

    python3 testbench/vendor-spa/mock_backend.py --port 8415
    open http://127.0.0.1:8415/auth

Accounts, the SMS code and the bundles live in `accounts.json` (synthetic). What the backend
answers was recorded from the production one with a test account; endpoints never called there
are filled from what the page reads (see README.md, "Fidelity").
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import pathlib
import secrets
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = pathlib.Path(__file__).resolve().parent
SITE = HERE / "site"

# The page shows its own copy when a request fails; it picks the copy by this class name.
WRONG_OTP = "DemoVendorWrongOTP"
NOT_REGISTERED = "DemoVendorNotRegistered"
ALREADY_REGISTERED = "DemoVendorAlreadyRegistered"
INVALID_MSISDN = "DemoVendorInvalidMSISDN"
INVALID_TOKEN = "DemoVendorInvalidToken"
NOT_ALLOWED = "DemoVendorNotAllowed"


class ApiError(Exception):
    def __init__(self, cls: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.cls, self.message, self.status = cls, message, status


def fake_jwt(login: str) -> str:
    """A token shaped like the PortaBilling JWT the real backend returns, not signed."""

    def part(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    now = int(time.time())
    claims = {
        "aud": ["portabilling-api", "portasip-api", "espf-api"],
        "exp": now + 2 * 24 * 3600,
        "iat": now,
        "login": login,
        "realm": "account",
        "jti": secrets.token_hex(8),
    }
    return f"{part({'alg': 'none'})}.{part(claims)}.mock"


class Backend:
    """In-memory state: issued registration tokens and sessions, per process."""

    def __init__(self, data: dict) -> None:
        self.data = data
        self.accounts = {a["msisdn"]: a for a in data["accounts"]}
        self.reg_tokens: dict[str, dict] = {}  # reg_token -> {msisdn, signup fields}
        self.sessions: dict[str, str] = {}  # access_token -> msisdn

    # -- helpers -------------------------------------------------------------------------------

    @staticmethod
    def msisdn(body: dict) -> str:
        value = "".join(ch for ch in str(body.get("msisdn", "")) if ch.isdigit())
        if len(value) < 9:
            raise ApiError(INVALID_MSISDN, "Invalid mobile number")
        return value

    def account_for(self, body: dict) -> dict:
        auth = body.get("auth") or {}
        msisdn = self.sessions.get(auth.get("access_token", ""))
        if not msisdn:
            raise ApiError(INVALID_TOKEN, "The token is invalid or has expired.", 401)
        return self.accounts[msisdn]

    def new_reg_token(self, msisdn: str, fields: dict | None = None) -> dict:
        token = secrets.token_hex(16)
        self.reg_tokens[token] = {"msisdn": msisdn, "fields": fields or {}}
        # The production backend answers otp: null; a hint would only make it easier to test.
        return {"reg_token": token, "otp": self.data.get("otp_hint")}

    def issue_session(self, msisdn: str) -> dict:
        access = fake_jwt(msisdn)
        self.sessions[access] = msisdn
        return {"access_token": access, "refresh_token": secrets.token_hex(16)}

    # -- endpoints -----------------------------------------------------------------------------

    def get(self, path: str, query: dict) -> dict:
        if path == "/sign-up/config":
            return {"version": self.data["version"], "countries": self.data["countries"]}
        raise ApiError("ApiError", "Not Found", 404)

    def post(self, path: str, body: dict) -> dict:
        handler = {
            "/sign-in": self.sign_in,
            "/sign-up": self.sign_up,
            "/otp/resend": self.otp_resend,
            "/otp/verify": self.otp_verify,
            "/external-login": self.external_login,
            "/welcome": lambda b: {"success": True},
            "/home-info": self.home_info,
            "/subscriptions": lambda b: {
                "records": self.account_for(b).get("history", []),
                "currency": self.data["currency"],
            },
            "/info/load": self.info_load,
            "/info/save": self.info_save,
            "/invite-preview": lambda b: (
                self.account_for(b)
                and {"message": self.data["invite_preview"], "allowed_prefixes": self.data["friend_prefixes"]}
            ),
            "/invite": lambda b: self.account_for(b) and {"success": True},
            "/friends/list": lambda b: {"friend_list": self.account_for(b).get("friends", [])},
            "/friends/pair": self.friends_pair,
            "/friends/unpair": self.friends_unpair,
            "/subscribe/precheck": self.precheck,
            "/subscribe/check-balance": self.check_balance,
            "/subscribe": self.subscribe,
            "/subscribe/status": lambda b: self.account_for(b) and {"status": 1},
        }.get(path)
        if handler is None:
            if path.startswith("/subscribe/stripe/"):
                raise ApiError(NOT_ALLOWED, "Card payments are not mocked.", 400)
            raise ApiError("ApiError", "Not Found", 404)
        return handler(body)

    def sign_in(self, body: dict) -> dict:
        msisdn = self.msisdn(body)
        account = self.accounts.get(msisdn)
        if not account or not account.get("registered"):
            raise ApiError(NOT_REGISTERED, "This number is not registered.")
        return self.new_reg_token(msisdn)

    def sign_up(self, body: dict) -> dict:
        msisdn = self.msisdn(body)
        account = self.accounts.get(msisdn)
        if account and account.get("registered"):
            raise ApiError(ALREADY_REGISTERED, "This number is already registered.")
        return self.new_reg_token(
            msisdn, {k: body.get(k) for k in ("first_name", "last_name", "alias", "email")}
        )

    def otp_resend(self, body: dict) -> dict:
        pending = self.reg_tokens.pop(body.get("reg_token", ""), None)
        if not pending:
            raise ApiError(INVALID_TOKEN, "The registration has expired.", 401)
        return self.new_reg_token(pending["msisdn"], pending["fields"])

    def otp_verify(self, body: dict) -> dict:
        pending = self.reg_tokens.get(body.get("reg_token", ""))
        if not pending:
            raise ApiError(INVALID_TOKEN, "The registration has expired.", 401)
        if str(body.get("otp")) != str(self.data["otp"]):
            raise ApiError(WRONG_OTP, "Wrong code.")
        del self.reg_tokens[body["reg_token"]]
        msisdn = pending["msisdn"]
        if msisdn not in self.accounts or not self.accounts[msisdn].get("registered"):
            fields = pending["fields"]
            self.accounts[msisdn] = {
                "msisdn": msisdn,
                "registered": True,
                "user_class": self.data["new_account_user_class"],
                **{k: v for k, v in fields.items() if v},
            }
        return self.issue_session(msisdn)

    def external_login(self, body: dict) -> dict:
        msisdn = self.data.get("external_token_accounts", {}).get(str(body.get("token")))
        if not msisdn:
            raise ApiError(INVALID_TOKEN, "The token is invalid or has expired.", 401)
        session = self.issue_session(msisdn)
        expires = (dt.datetime.now(dt.UTC) + dt.timedelta(days=2)).isoformat()
        return {"auth": {**session, "expires_at": expires}, "user_class": self.accounts[msisdn]["user_class"]}

    def home_info(self, body: dict) -> dict:
        account = self.account_for(body)
        return {
            "msisdn": account["msisdn"],
            "user_class": account.get("user_class"),
            "allow_subscribe": True,
            "allow_invite": True,
            "allow_friends": True,
            "allow_history": True,
            "external_token": "mock-external-token",
            "active_subscription": account.get("active_subscription"),
            "version": self.data["version"],
        }

    def info_load(self, body: dict) -> dict:
        account = self.account_for(body)
        return {
            "msisdn": account["msisdn"],
            "first_name": account.get("first_name", ""),
            "last_name": account.get("last_name", ""),
            "alias": account.get("alias", ""),
            "email": account.get("email", ""),
            "discount_code": None,
            "discount_code_mode": None,
            "extra_info": None,
        }

    def info_save(self, body: dict) -> dict:
        account = self.account_for(body)
        for key in ("first_name", "last_name", "alias", "email"):
            if body.get(key) is not None:
                account[key] = body[key]
        return {"success": True}

    def friends_pair(self, body: dict) -> dict:
        account = self.account_for(body)
        msisdn = "".join(
            ch for ch in str(body.get("msisdn") or body.get("friend_msisdn") or "") if ch.isdigit()
        )
        if msisdn:
            account.setdefault("friends", []).append({"msisdn": msisdn, "alias": body.get("alias", "")})
        return {"success": True, "friend_list": account.get("friends", [])}

    def friends_unpair(self, body: dict) -> dict:
        account = self.account_for(body)
        account["friends"] = [f for f in account.get("friends", []) if f["msisdn"] != str(body.get("msisdn"))]
        return {"friend_list": account["friends"]}

    def precheck(self, body: dict) -> dict:
        account = self.account_for(body)
        return {
            "active_subscription": account.get("active_subscription"),
            "bundles": self.data["bundles"],
            "currency": self.data["currency"],
            "discount_available": False,
            "providers": self.data["providers"],
            "external_token": "mock-external-token",
        }

    def check_balance(self, body: dict) -> dict:
        account = self.account_for(body)
        return {
            "active_subscription": account.get("active_subscription"),
            "balance": account.get("balance", 100),
            "enough_balance": True,
            "currency": self.data["currency"],
            "discount_available": False,
            "providers": self.data["providers"],
            "external_token": "mock-external-token",
        }

    def subscribe(self, body: dict) -> dict:
        account = self.account_for(body)
        bundle = next(
            (b for b in self.data["bundles"] if b["id"] == body.get("bundle_id")), self.data["bundles"][0]
        )
        now = dt.datetime.now(dt.UTC)
        account["active_subscription"] = {
            "expiration_date": (now + dt.timedelta(days=bundle["period"])).isoformat(),
            "remaining_seconds": bundle["volume"] * 60,
        }
        account.setdefault("history", []).insert(
            0,
            {
                "start": now.isoformat(),
                "end": account["active_subscription"]["expiration_date"],
                "volume": bundle["volume"],
                "period": bundle["period"],
                "price": bundle["price"],
            },
        )
        return {"success": True}


class Handler(BaseHTTPRequestHandler):
    backend: Backend
    quiet = False

    def log_message(self, fmt: str, *args) -> None:
        if not self.quiet:
            super().log_message(fmt, *args)

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("cache-control", "no-store")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, obj: dict) -> None:
        self._send(status, json.dumps(obj).encode(), "application/json")

    def _api(self, method: str) -> None:
        url = urlparse(self.path)
        path = url.path[len("/api/v1") :] or "/"
        body: dict = {}
        if method == "POST":
            length = int(self.headers.get("content-length") or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                body = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                body = {}
        try:
            if method == "GET":
                result = self.backend.get(path, parse_qs(url.query))
            else:
                result = self.backend.post(path, body)
            if not self.quiet:
                print(f"  {method} /api/v1{path} -> 200 {json.dumps(result)[:200]}")
            self._json(200, result)
        except ApiError as e:
            if e.status == 404:
                self._json(404, {"detail": "Not Found"})
                return
            print(f"  {method} /api/v1{path} -> {e.status} {e.cls}")
            self._json(e.status, {"class": e.cls, "message": e.message, "code": e.cls, "details": None})

    def _static(self) -> None:
        url = urlparse(self.path)
        rel = url.path.lstrip("/")
        target = (SITE / rel).resolve()
        if rel and target.is_file() and SITE in target.parents:
            types = {
                ".js": "text/javascript",
                ".css": "text/css",
                ".svg": "image/svg+xml",
                ".html": "text/html",
            }
            self._send(200, target.read_bytes(), types.get(target.suffix, "application/octet-stream"))
            return
        # A single-page app: every other path is a route of the page.
        page = (SITE / "index.html").read_text(encoding="utf-8")
        dev = parse_qs(url.query).get("dev_payload", [None])[0]
        if dev and dev in self.backend.accounts:
            # Browser-only development of the logged-in screens: do what the app's payload
            # injection does, with a session for that account.
            session = self.backend.issue_session(dev)
            payload = {
                "externalPageToken": {
                    "accessToken": session["access_token"],
                    "refreshToken": session["refresh_token"],
                    "expiresAt": None,
                }
            }
            script = (
                "<script>setTimeout(function(){window.onPayloadDataReady&&"
                f"window.onPayloadDataReady({json.dumps(payload)})}},1500)</script>"
            )
            page = page.replace("</body>", script + "</body>", 1)
        self._send(200, page.encode(), "text/html; charset=utf-8")

    def do_GET(self) -> None:
        if self.path.startswith("/api/v1/"):
            self._api("GET")
        else:
            self._static()

    def do_POST(self) -> None:
        if self.path.startswith("/api/v1/"):
            self._api("POST")
        else:
            self._json(404, {"detail": "Not Found"})


def make_server(host: str, port: int, accounts: pathlib.Path, quiet: bool = False) -> ThreadingHTTPServer:
    data = json.loads(accounts.read_text(encoding="utf-8"))
    handler = type("BoundHandler", (Handler,), {"backend": Backend(data), "quiet": quiet})
    return ThreadingHTTPServer((host, port), handler)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8415)
    parser.add_argument("--accounts", type=pathlib.Path, default=HERE / "accounts.json")
    args = parser.parse_args()
    server = make_server(args.host, args.port, args.accounts)
    print(f"vendor page + mock backend on http://{args.host}:{args.port}/auth  (accounts: {args.accounts})")
    server.serve_forever()


if __name__ == "__main__":
    main()

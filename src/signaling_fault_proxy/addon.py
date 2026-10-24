"""The mitmproxy addon: forwards the app's HTTP and signaling to the upstream Core as is, logs every
signaling frame and applies the armed rules. Controlled over HTTP on the proxy's own port:

    GET /__proxy/status                 armed rules and live signaling connections
    GET /__proxy/arm?rule=<name>[&<param>=<value>...]   arm a rule (see rules.RULES)
    GET /__proxy/lift[?rule=<name>]     lift one rule, or all of them
"""

from __future__ import annotations

import importlib.util
import json
import logging
import time
from pathlib import Path
from urllib.parse import urlsplit

from mitmproxy import ctx, http

from signaling_fault_proxy.rules import RULES, Action, Frame, Rules

CONTROL_PREFIX = "/__proxy/"
SIGNALING_PATH = "/signaling/v1"
SIGNUP_SUFFIX = "/api/v1/user"
OTP_VERIFY_SUFFIX = "/api/v1/session/otp-verify"
OTP_CREATE_SUFFIX = "/api/v1/session/otp-create"
DASHBOARD_FILE = Path(__file__).with_name("dashboard.html")
# Fields the app's signup body (SessionUserCredential) already carries that a login
# (SessionLoginCredential) also needs - kept so the device identity stays the same.
LOGIN_KEEP_FIELDS = ("identifier", "type", "bundle_id")

log = logging.getLogger("signaling-fault-proxy")


class SignalingFaultProxy:
    def __init__(self) -> None:
        self.rules = Rules()
        self.live: list[http.HTTPFlow] = []
        self._journal = None
        self._signup_login: tuple[str, str] | None = None
        self._upstream: str | None = None  # the Core to forward to, set at start or from the dashboard
        self._control_token = ""  # if set, control calls must carry ?token= (guards a public instance)
        self._rewrite_signup = True  # off -> custom signup goes to Core untouched (real signup backend)
        self._signup_mock: tuple[int, str] | None = None  # (status, body): answer signup with this, no Core
        self._mock_pending_login: dict | None = None  # creds from an OTP-mock signup, replayed on otp-verify
        self._signups = 0  # signup POSTs seen since start, answered by a mock or sent to the Core

    def load(self, loader) -> None:
        loader.add_option("journal", str, "", "file to append every signaling frame to (JSON lines)")
        loader.add_option("arm", str, "", "rules to arm at start: name[?param=value&...], comma-separated")
        loader.add_option("rules", str, "", "python files with more rules, comma-separated")
        loader.add_option(
            "signup_as_login",
            str,
            "",
            "test stub: turn POST /api/v1/user (custom signup) into a normal login with these "
            "credentials, as 'login:password'. The stand has no custom-signup backend.",
        )
        loader.add_option("upstream", str, "", "initial Core to forward to; the dashboard can change it")
        loader.add_option("control_token", str, "", "if set, /__proxy/ calls must carry ?token=<it>")

    def configure(self, updated) -> None:
        if "journal" in updated and ctx.options.journal:
            path = Path(ctx.options.journal)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._journal = path.open("a", buffering=1)
        if "rules" in updated and ctx.options.rules:
            for path in ctx.options.rules.split(","):
                if path.strip():
                    _load_rules(Path(path.strip()))
                    self._note(f"rules loaded from {path.strip()}")
        if "arm" in updated and ctx.options.arm:
            for spec in ctx.options.arm.split(","):
                if spec.strip():
                    name, _, query = spec.strip().partition("?")
                    params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
                    self.rules.arm(name, params)
                    self._note(f"armed {spec.strip()}")
        if "signup_as_login" in updated and ctx.options.signup_as_login:
            self._set_signup(ctx.options.signup_as_login)
        if "upstream" in updated and ctx.options.upstream:
            self._set_core(ctx.options.upstream)
        if "control_token" in updated:
            self._control_token = ctx.options.control_token

    # -- control ------------------------------------------------------------------------------

    def request(self, flow: http.HTTPFlow) -> None:
        path = flow.request.path
        if not path.startswith(CONTROL_PREFIX):
            if self._is_signup_post(flow.request):
                self._signups += 1
                self._note(f"signup #{self._signups}: {'mock ' + str(self._signup_mock[0]) if self._signup_mock else 'to the Core'}")
            if self._signup_mock is not None:
                resp = self._mock_response(flow.request)  # may also rewrite the request for a real login
                if resp is not None:
                    flow.response = resp
                    return
            if self._upstream is None:
                flow.response = _json(503, {"error": "no Core set - open /__proxy/ and set one"})
                return
            self._route(flow.request)
            self._signup_as_login(flow.request)  # no-ops unless it is a signup carrying credentials
            return

        command = flow.request.path_components[1] if len(flow.request.path_components) > 1 else ""
        if command in ("", "dashboard"):  # the dashboard page is public (it only talks to the API)
            flow.response = self._dashboard()
            return
        query = dict(flow.request.query)
        token = query.pop("token", None)
        try:
            if self._control_token and token != self._control_token:
                raise ValueError("bad or missing ?token=")
            rule = query.pop("rule", None)
            if command == "arm":
                if not rule:
                    raise ValueError("arm needs ?rule=<name>")
                armed = self.rules.arm(rule, query)
                self._note(f"armed {armed.describe()}")
            elif command == "lift":
                self.rules.lift(rule)
                self._note(f"lifted {rule or 'all rules'}")
            elif command == "core":
                self._set_core(query["url"]) if query.get("url") else None
            elif command == "signup":
                self._set_signup(
                    "" if query.get("off") else f"{query.get('login', '')}:{query.get('password', '')}"
                )
            elif command == "rewrite":
                self._rewrite_signup = not query.get("off")
                self._note(f"signup rewrite {'on' if self._rewrite_signup else 'off (Core handles signup)'}")
            elif command == "mock":
                self._set_mock(None if query.get("off") else (int(query.get("status", 200)), query.get("body", "")))
            elif command != "status":
                raise ValueError(f"unknown command {command!r}")
            flow.response = _json(200, self._status())
        except (ValueError, KeyError) as e:
            flow.response = _json(400, {"error": str(e), "rules": sorted(RULES)})

    def _is_signup_post(self, req: http.Request) -> bool:
        return req.method == "POST" and req.path.split("?", 1)[0].rstrip("/").endswith(SIGNUP_SUFFIX)

    def _mock_is_otp(self) -> bool:
        if self._signup_mock is None:
            return False
        try:
            return "otp_id" in json.loads(self._signup_mock[1] or "{}")
        except ValueError:
            return False

    def _mock_response(self, req: http.Request) -> http.Response | None:
        """The answer a configured mock gives, or None to let the request go to the Core (possibly
        after a rewrite). An OTP mock also stands in for the otp-create/otp-verify steps, so the whole
        OTP flow completes: the credentials the signup carried are replayed as a real login on verify,
        so the app ends up in a genuine session - whatever code the user types."""
        status, body = self._signup_mock
        canned = http.Response.make(status, body, {"content-type": "application/json"})
        if self._is_signup_post(req):
            if self._mock_is_otp():
                self._stash_pending_login(req)
            return canned
        if not self._mock_is_otp():
            return None
        path = req.path.split("?", 1)[0].rstrip("/")
        if req.method == "POST" and path.endswith(OTP_CREATE_SUFFIX):
            return canned  # a resend hands back the same otp
        if req.method == "POST" and path.endswith(OTP_VERIFY_SUFFIX):
            if self._mock_pending_login is not None:
                self._rewrite_otp_verify_to_login(req)
                return None  # forward to Core as a real login
            return _json(200, {"token": "mock-token", "user_id": "1", "tenant_id": ""})
        return None

    def _stash_pending_login(self, req: http.Request) -> None:
        try:
            body = json.loads(req.get_text() or "{}")
        except ValueError:
            body = {}
        login = (body.get("login") or "").strip()
        if login:
            keep = {k: body[k] for k in LOGIN_KEEP_FIELDS if k in body}
            keep["login"] = login
            keep["password"] = body.get("password", "")
            self._mock_pending_login = keep
        else:
            self._mock_pending_login = None

    def _rewrite_otp_verify_to_login(self, req: http.Request) -> None:
        base, sep, query = req.path.partition("?")
        b = base.rstrip("/")
        if b.endswith(OTP_VERIFY_SUFFIX):
            b = b[: -len("/otp-verify")]  # .../session/otp-verify -> .../session
        req.set_text(json.dumps(self._mock_pending_login))
        req.path = b + sep + query
        self._note(f"otp mock: verify -> real login as {self._mock_pending_login.get('login')}")

    def _set_mock(self, mock: tuple[int, str] | None) -> None:
        self._mock_pending_login = None
        if mock is None:
            self._signup_mock = None
            self._note("signup mock off")
            return
        status, body = mock
        try:
            json.loads(body or "{}")
        except ValueError:
            raise ValueError("mock body must be JSON")
        self._signup_mock = (status, body or "{}")
        self._note(f"signup mock on: {status} {self._signup_mock[1][:80]}")

    def _signup_as_login(self, req: http.Request) -> None:
        """Rewrite `POST .../api/v1/user` (custom signup) into `POST .../api/v1/session` (login).

        The stand has no custom-signup backend, so instead of the signup the app's request is turned
        into a normal login. The credentials come from the signup body (a login/password the page
        collected); if the body carries none, a default set on the proxy is used, and if there is no
        default either the signup is left untouched - so a proxy that holds no account logs nobody in
        unless the request itself carries who to log in as. Both endpoints answer with the same
        SessionToken body, which the app parses as a signup result, so the login goes through as if
        the signup returned it. The tenant prefix, if any, is kept - only the last segment changes."""
        if not self._rewrite_signup or not self._is_signup_post(req):
            return  # off, or not a signup -> let the Core handle it
        base, sep, query = req.path.partition("?")
        try:
            body = json.loads(req.get_text() or "{}")
        except ValueError:
            body = {}
        body_login = (body.get("login") or "").strip()
        if body_login:
            login, password = body_login, body.get("password", "")
        elif self._signup_login is not None:
            login, password = self._signup_login
        else:
            return  # no credentials anywhere - leave the signup as a plain signup
        rewritten = {k: body[k] for k in LOGIN_KEEP_FIELDS if k in body}
        rewritten["login"] = login
        rewritten["password"] = password
        req.set_text(json.dumps(rewritten))
        req.path = base.rstrip("/")[: -len("user")] + "session" + sep + query
        self._note(f"signup-as-login: POST {base} -> {req.path.split('?')[0]} as {login}")

    # -- runtime configuration (from the dashboard or the command line) -------------------------

    def _set_core(self, url: str) -> None:
        u = urlsplit(url.strip())
        if u.scheme not in ("http", "https") or not u.hostname:
            raise ValueError("Core must be an http(s) URL")
        self._upstream = f"{u.scheme}://{u.netloc}"
        self._note(f"Core set to {self._upstream}")

    def _set_signup(self, spec: str) -> None:
        if not spec or spec == ":":
            self._signup_login = None
            self._note("signup-as-login off")
            return
        login, sep, password = spec.partition(":")
        if not sep or not login:
            raise ValueError("signup credentials must be 'login:password'")
        self._signup_login = (login, password)
        self._note(f"signup-as-login on: custom signup logs in as {login}")

    def _route(self, req: http.Request) -> None:
        """Send the request to the currently configured Core, whatever the proxy was launched with."""
        u = urlsplit(self._upstream)
        req.scheme = u.scheme
        req.host = u.hostname
        req.port = u.port or (443 if u.scheme == "https" else 80)

    def _dashboard(self) -> http.Response:
        try:
            html = DASHBOARD_FILE.read_bytes()
        except OSError:
            return _json(500, {"error": "dashboard.html is missing"})
        return http.Response.make(200, html, {"content-type": "text/html; charset=utf-8"})

    def _status(self) -> dict:
        return {
            "core": self._upstream,
            "rewrite_signup": self._rewrite_signup,
            "signup_mock": {"status": self._signup_mock[0], "body": self._signup_mock[1]} if self._signup_mock else None,
            "signup_as_login": self._signup_login[0] if self._signup_login else None,  # login only
            "armed": self.rules.describe(),
            "signaling_connections": len([f for f in self.live if f.websocket is not None]),
            "signups": self._signups,
            "rules": sorted(RULES),
        }

    # -- signaling ------------------------------------------------------------------------------

    def websocket_start(self, flow: http.HTTPFlow) -> None:
        if flow.request.path.split("?")[0].endswith(SIGNALING_PATH):
            self.live.append(flow)
            self._note(f"signaling connected from {flow.client_conn.peername}")

    def websocket_end(self, flow: http.HTTPFlow) -> None:
        if flow in self.live:
            self.live.remove(flow)
            code = flow.websocket.close_code if flow.websocket else None
            self._note(f"signaling closed (code {code})")

    def websocket_message(self, flow: http.HTTPFlow) -> None:
        if flow not in self.live:
            return
        message = flow.websocket.messages[-1]
        if not message.is_text:
            return
        frame = Frame.parse(message.from_client, message.text)
        action, by = self.rules.on_frame(frame)
        self._record(frame, action, by)
        if action is Action.DROP:
            message.drop()
        elif action is Action.CLOSE:
            message.drop()
            self._note(f"{by}: closing the signaling on {frame.describe()}")
            self._close(flow)

    def _close(self, flow: http.HTTPFlow) -> None:
        """Close both sides of the connection at once, with no close frame - as a socket that goes.
        `flow.kill()` does not do it for a WebSocket: mitmproxy only stops forwarding the message."""
        handler = ctx.master.addons.get("proxyserver").connections.get(flow.client_conn.id)
        if handler is None:
            self._note("the connection is gone already")
            return
        for io in list(handler.transports.values()):
            if io.writer is not None and not io.writer.is_closing():
                io.writer.close()

    # -- output ---------------------------------------------------------------------------------

    def _record(self, frame: Frame, action: Action, by: str | None) -> None:
        suffix = f"  [{action.value} by {by}]" if by else ""
        log.info("%s%s", frame.describe(), suffix)
        if self._journal:
            self._journal.write(
                json.dumps(
                    {
                        "t": round(time.time(), 3),
                        "from_client": frame.from_client,
                        "kind": frame.kind,
                        "name": frame.name,
                        "transaction": frame.transaction,
                        "action": action.value,
                        "rule": by,
                    }
                )
                + "\n"
            )

    def _note(self, text: str) -> None:
        log.warning("%s", text)
        if self._journal:
            self._journal.write(json.dumps({"t": round(time.time(), 3), "note": text}) + "\n")


def _json(status: int, body: dict) -> http.Response:
    return http.Response.make(status, json.dumps(body, indent=2) + "\n", {"content-type": "application/json"})


def _load_rules(path: Path) -> None:
    """Imports a file of rules: its `@rule` classes register themselves on import."""
    spec = importlib.util.spec_from_file_location(f"signaling_fault_proxy_rules_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load rules from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)


addons = [SignalingFaultProxy()]

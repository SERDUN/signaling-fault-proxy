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

from mitmproxy import ctx, http

from signaling_fault_proxy.rules import RULES, Action, Frame, Rules

CONTROL_PREFIX = "/__proxy/"
SIGNALING_PATH = "/signaling/v1"
SIGNUP_SUFFIX = "/api/v1/user"
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
        if "signup_as_login" in updated:
            spec = ctx.options.signup_as_login
            if spec:
                login, sep, password = spec.partition(":")
                if not sep or not login:
                    raise ValueError("signup_as_login must be 'login:password'")
                self._signup_login = (login, password)
                self._note(f"signup-as-login on: custom signup logs in as {login}")
            else:
                self._signup_login = None

    # -- control ------------------------------------------------------------------------------

    def request(self, flow: http.HTTPFlow) -> None:
        path = flow.request.path
        if not path.startswith(CONTROL_PREFIX):
            if self._signup_login is not None:
                self._signup_as_login(flow.request)
            return
        command = flow.request.path_components[1] if len(flow.request.path_components) > 1 else ""
        query = dict(flow.request.query)
        rule = query.pop("rule", None)
        try:
            if command == "arm":
                if not rule:
                    raise ValueError("arm needs ?rule=<name>")
                armed = self.rules.arm(rule, query)
                self._note(f"armed {armed.describe()}")
            elif command == "lift":
                self.rules.lift(rule)
                self._note(f"lifted {rule or 'all rules'}")
            elif command != "status":
                raise ValueError(f"unknown command {command!r}")
            body, status = self._status(), 200
        except ValueError as e:
            body, status = {"error": str(e), "rules": sorted(RULES)}, 400
        flow.response = http.Response.make(
            status, json.dumps(body, indent=2) + "\n", {"content-type": "application/json"}
        )

    def _signup_as_login(self, req: http.Request) -> None:
        """Rewrite `POST .../api/v1/user` (custom signup) into `POST .../api/v1/session` (login).

        The stand has no custom-signup backend, so instead of the signup the app's request is turned
        into a normal login with the configured test credentials. Both endpoints answer with the same
        SessionToken body, which the app parses as a signup result, so the login goes through as if
        the signup returned it. The tenant prefix, if any, is kept - only the last segment changes."""
        base, sep, query = req.path.partition("?")
        if req.method != "POST" or not base.rstrip("/").endswith(SIGNUP_SUFFIX):
            return
        assert self._signup_login is not None
        try:
            body = json.loads(req.get_text() or "{}")
        except ValueError:
            body = {}
        # Credentials from the signup body win, so a page can log in as a specific account; an empty
        # (or absent) login falls back to the account the proxy was configured with.
        default_login, default_password = self._signup_login
        body_login = (body.get("login") or "").strip()
        if body_login:
            login, password = body_login, body.get("password", "")
        else:
            login, password = default_login, default_password
        rewritten = {k: body[k] for k in LOGIN_KEEP_FIELDS if k in body}
        rewritten["login"] = login
        rewritten["password"] = password
        req.set_text(json.dumps(rewritten))
        req.path = base.rstrip("/")[: -len("user")] + "session" + sep + query
        self._note(f"signup-as-login: POST {base} -> {req.path.split('?')[0]} as {login}")

    def _status(self) -> dict:
        return {
            "upstream": ctx.options.mode,
            "armed": self.rules.describe(),
            "signaling_connections": len([f for f in self.live if f.websocket is not None]),
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


def _load_rules(path: Path) -> None:
    """Imports a file of rules: its `@rule` classes register themselves on import."""
    spec = importlib.util.spec_from_file_location(f"signaling_fault_proxy_rules_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load rules from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)


addons = [SignalingFaultProxy()]

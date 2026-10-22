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

log = logging.getLogger("signaling-fault-proxy")


class SignalingFaultProxy:
    def __init__(self) -> None:
        self.rules = Rules()
        self.live: list[http.HTTPFlow] = []
        self._journal = None

    def load(self, loader) -> None:
        loader.add_option("journal", str, "", "file to append every signaling frame to (JSON lines)")
        loader.add_option("arm", str, "", "rules to arm at start: name[?param=value&...], comma-separated")
        loader.add_option("rules", str, "", "python files with more rules, comma-separated")

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

    # -- control ------------------------------------------------------------------------------

    def request(self, flow: http.HTTPFlow) -> None:
        path = flow.request.path
        if not path.startswith(CONTROL_PREFIX):
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

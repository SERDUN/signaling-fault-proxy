"""An example of a rule kept outside the proxy: load it with

    uv run signaling-fault-proxy --upstream https://core.example.com --rules examples/drop_acks.py

and arm it with `curl 'http://127.0.0.1:8080/__proxy/arm?rule=drop-acks-for&request=hold'`.
"""

from dataclasses import dataclass, field

from signaling_fault_proxy.rules import Action, Frame, Rule, rule


@rule("drop-acks-for")
@dataclass
class DropAcksFor(Rule):
    """Core's answers to one kind of the app's request never reach the app: the request waits for
    its answer until the client's transaction timeout, while everything else passes."""

    request: str = "update"
    _waiting: set[str] = field(default_factory=set, init=False)

    def on_frame(self, frame: Frame) -> Action:
        if frame.from_client and frame.kind == "request" and frame.name == self.request:
            if frame.transaction:
                self._waiting.add(frame.transaction)
            return Action.PASS
        if not frame.from_client and frame.transaction in self._waiting:
            return Action.DROP
        return Action.PASS

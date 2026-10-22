# Rules

A rule looks at every signaling frame (`/signaling/v1`, text frames) and says what happens to it:

| Action | Effect |
|---|---|
| `pass` | forwarded as is |
| `drop` | not forwarded; the connection stays |
| `close` | not forwarded, and both sides of the connection are closed at once, with no WebSocket close frame - the app sees an abnormal closure (1006) and fails every request still waiting for an answer |

Armed rules are asked in the order they were armed; the first one that does not `pass` decides. A
one-shot rule lifts itself once it has acted. A rule takes parameters when it is armed:

```sh
curl 'http://127.0.0.1:8080/__proxy/arm?rule=close-on&from=app&name=hold&after=update'
uv run signaling-fault-proxy --upstream https://core.example.com --arm 'close-on?from=app&name=hold'
```

There are three ways to get the fault a test needs, from no code to some:

1. a **generic rule** with parameters - `close-on`, `drop-on`;
2. a **named scenario** that ships with the proxy - `close-trickle-after-update`, `blackhole`, `close-now`;
3. a **rule of your own** in a Python file, loaded with `--rules` - no change to the proxy.

## Generic rules

`close-on` closes the connection on the matching frame; `drop-on` drops the matching frames and keeps
the connection. Both take:

| Parameter | Values | Default |
|---|---|---|
| `from` | `app`, `core`, or empty for either | either |
| `kind` | `request`, `response`, `event`, `handshake`, or empty | any |
| `name` | the frame's type: `update`, `ice_trickle`, `hold`, `ack`, `keepalive`, `accepted`, ... | any |
| `after` | act only once a frame of this name has passed (either way) | - |
| `times` | how many matching frames to act on before lifting itself; `0` - until lifted | `1` |

Examples:

```sh
# the app's hold request never reaches Core, once
curl 'http://127.0.0.1:8080/__proxy/arm?rule=drop-on&from=app&name=hold'
# Core's keepalive answers are lost until lifted - the client's keepalive times out
curl 'http://127.0.0.1:8080/__proxy/arm?rule=drop-on&from=core&name=keepalive&times=0'
# the socket goes when Core sends the answer to an ICE restart
curl 'http://127.0.0.1:8080/__proxy/arm?rule=close-on&from=core&name=accepted&after=update'
```

Frame names are those of the app's signaling protocol: a request's `request` field, a response's
`response`, an event's `event`, a handshake's `handshake`. The proxy's log shows them for every
frame (`app -> core request:update ...`), which is the quickest way to find the one you need.

## Named scenarios

### `close-trickle-after-update`

An `update` from the app arms it; the next `ice_trickle` that carries a candidate (not the
end-of-candidates one) closes the connection. One-shot.

After an ICE restart the app sends `update` with the new offer and trickles its new candidates right
after. Closing the socket while a candidate still waits for Core's answer gives what a client sees
when its keepalive gets no reply on a real network: every queued transaction fails as terminated by
the disconnect. See [the reproduction](reproduce/lost-trickle-after-ice-restart.md). It differs
from `close-on?from=app&name=ice_trickle&after=update` only in skipping the end-of-candidates request.

Arm it shortly before you cause the ICE restart: any `update` arms it, a hold or an answer to the far
end's re-INVITE included.

### `blackhole`

Drops every frame both ways until it is lifted. Nothing is closed and the OS reports nothing - the
signaling side of a silent loss of the network. Requests wait for their answers; the client's
keepalive is what notices, after its own timeout.

```sh
curl 'http://127.0.0.1:8080/__proxy/arm?rule=blackhole'
sleep 40
curl 'http://127.0.0.1:8080/__proxy/lift?rule=blackhole'
```

### `close-now`

Closes every signaling connection on its next frame, from either side. One-shot. The quickest check
that the app reconnects and that the proxy really closes: the app's log shows the socket closed with
code 1006 and a new connect a moment later.

## A rule of your own

A rule is a dataclass derived from `Rule`, registered by name with `@rule`. Its fields are its
parameters (str, int and bool are converted from the query). Keep it in your test project and load
it at start - the proxy needs no change:

```python
# my_rules.py
from dataclasses import dataclass, field

from signaling_fault_proxy.rules import Action, Frame, Rule, rule


@rule("drop-acks-for")
@dataclass
class DropAcksFor(Rule):
    """Core's answers to one kind of request never reach the app."""

    request: str = "update"
    _waiting: set[str] = field(default_factory=set, init=False)

    def on_frame(self, frame: Frame) -> Action:
        if frame.from_client and frame.kind == "request" and frame.name == self.request:
            self._waiting.add(frame.transaction or "")
            return Action.PASS
        if not frame.from_client and frame.transaction in self._waiting:
            return Action.DROP
        return Action.PASS
```

```sh
uv run signaling-fault-proxy --upstream https://core.example.com --rules my_rules.py
curl 'http://127.0.0.1:8080/__proxy/arm?rule=drop-acks-for&request=hold'
```

A one-shot rule sets `one_shot = True` and `self.fired = True` when it has acted. `Frame` gives the
direction (`from_client`), the kind, the name, the transaction and the parsed body. The full example
is [`examples/drop_acks.py`](../examples/drop_acks.py).

A rule that turns out to be useful for more than one test belongs in `rules.py` with a test in
`tests/` and a line in this page.

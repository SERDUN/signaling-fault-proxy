"""What the proxy does to the signaling, kept apart from mitmproxy so it can be tested alone.

A rule looks at every signaling frame and returns what to do with it: pass it on, drop it, or close
the connection. Rules are registered by name with `@rule`, armed and lifted at run time with
parameters (`arm?rule=close-on&request=hold`), and a one-shot rule lifts itself once it has acted.

Most scenarios need no code: `close-on` and `drop-on` match frames by direction, kind, name and the
frame before them. A scenario that needs more is a `Rule` subclass in a module of its own, loaded
with `--rules <file>` - see docs/rules.md.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar, TypeVar


class Action(Enum):
    PASS = "pass"
    DROP = "drop"
    CLOSE = "close"


@dataclass(frozen=True)
class Frame:
    """One signaling frame as the rules see it."""

    from_client: bool
    kind: str | None  # "request", "response", "event", "handshake" or None when not JSON
    name: str | None  # the request / response / event / handshake type, e.g. "update"
    transaction: str | None
    body: dict[str, Any] | None

    @classmethod
    def parse(cls, from_client: bool, text: str) -> Frame:
        try:
            body = json.loads(text)
        except ValueError:
            return cls(from_client, None, None, None, None)
        if not isinstance(body, dict):
            return cls(from_client, None, None, None, None)
        for kind in ("request", "response", "event", "handshake"):
            if kind in body:
                return cls(from_client, kind, str(body[kind]), body.get("transaction"), body)
        return cls(from_client, None, None, body.get("transaction"), body)

    def describe(self) -> str:
        arrow = "app -> core" if self.from_client else "core -> app"
        what = f"{self.kind}:{self.name}" if self.kind else "?"
        tx = f" tx={self.transaction}" if self.transaction else ""
        return f"{arrow} {what}{tx}"


@dataclass
class Rule:
    """Base: a rule sees each frame and says what happens to it.

    Fields of a subclass are its parameters: `arm?rule=<name>&<field>=<value>` sets them, converted
    to the field's type (str, int, bool)."""

    name: ClassVar[str] = "rule"
    one_shot: bool = False
    fired: bool = field(default=False, init=False)

    def on_frame(self, frame: Frame) -> Action:
        return Action.PASS

    def describe(self) -> dict[str, Any]:
        params = {
            f.name: getattr(self, f.name)
            for f in dataclasses.fields(self)
            if f.init and not f.name.startswith("_")
        }
        return {"rule": self.name, **{_PUBLIC.get(k, k): v for k, v in params.items()}}


R = TypeVar("R", bound=type[Rule])

# Field names that clash with Python or with `Rule.name`, and the parameter names people type.
_PUBLIC = {"from_": "from", "name_": "name"}

RULES: dict[str, type[Rule]] = {}


def rule(name: str) -> Callable[[R], R]:
    """Registers a `Rule` subclass under `name`, so it can be armed by it."""

    def register(cls: R) -> R:
        if name in RULES and RULES[name] is not cls:
            raise ValueError(f"rule {name!r} is registered already")
        cls.name = name
        RULES[name] = cls
        return cls

    return register


def _convert(value: str, to: Any) -> Any:
    if to in (bool, "bool", "bool | None"):
        return value.lower() in ("1", "true", "yes", "on")
    if to in (int, "int", "int | None"):
        return int(value)
    return value


def build(name: str, params: dict[str, str] | None = None) -> Rule:
    """A new rule `name` with its parameters set from strings."""
    try:
        cls = RULES[name]
    except KeyError:
        raise ValueError(f"unknown rule {name!r}; known: {', '.join(sorted(RULES))}") from None
    fields = {f.name: f for f in dataclasses.fields(cls) if f.init and not f.name.startswith("_")}
    kwargs = {}
    for key, value in (params or {}).items():
        if key not in fields:
            known = ", ".join(_PUBLIC.get(f, f) for f in fields) or "none"
            raise ValueError(f"rule {name!r} has no parameter {_PUBLIC.get(key, key)!r}; it has: {known}")
        kwargs[key] = _convert(value, fields[key].type)
    return cls(**kwargs)


# -- generic rules: match frames, no code needed ----------------------------------------------------


@dataclass
class Match(Rule):
    """Matches frames by what they are and, optionally, by a frame seen before them.

    from_     "app", "core" or "" for either
    kind      "request", "response", "event", "handshake" or "" for any
    name      the frame's type, e.g. "update", "ice_trickle", "ack", or "" for any
    after     only once a frame of this name has passed (either way), e.g. "update"
    times     how many matching frames to act on before lifting itself; 0 - until lifted
    """

    from_: str = ""
    kind: str = ""
    name_: str = ""
    after: str = ""
    times: int = 1
    _seen_after: bool = field(default=False, init=False)
    _acted: int = field(default=0, init=False)

    action: ClassVar[Action] = Action.PASS

    def __post_init__(self) -> None:
        self.one_shot = self.times > 0

    def matches(self, frame: Frame) -> bool:
        if self.from_ and (frame.from_client != (self.from_ == "app")):
            return False
        if self.kind and frame.kind != self.kind:
            return False
        return not self.name_ or frame.name == self.name_

    def on_frame(self, frame: Frame) -> Action:
        if self.after and not self._seen_after:
            if frame.name == self.after:
                self._seen_after = True
            return Action.PASS
        if not self.matches(frame):
            return Action.PASS
        self._acted += 1
        if self.times and self._acted >= self.times:
            self.fired = True
        return self.action


def _params_alias(params: dict[str, str] | None) -> dict[str, str]:
    """`from` and `name` are what people type; the fields are `from_` and `name_`."""
    private = {v: k for k, v in _PUBLIC.items()}
    return {private.get(key, key): value for key, value in (params or {}).items()}


@rule("close-on")
@dataclass
class CloseOn(Match):
    """Closes the connection on the matching frame (the frame is not forwarded)."""

    action: ClassVar[Action] = Action.CLOSE


@rule("drop-on")
@dataclass
class DropOn(Match):
    """Drops the matching frames; the connection stays."""

    action: ClassVar[Action] = Action.DROP


# -- named scenarios ----------------------------------------------------------------------------------


@rule("close-trickle-after-update")
@dataclass
class CloseOnTrickleAfterUpdate(Rule):
    """The WT-2071 case: after the app sends an `update` (an ICE restart offer, say), the first
    candidate it trickles closes the connection before the candidate reaches Core - so the
    request is still waiting for its answer when the socket goes, as when the client's keepalive
    got no reply on a real phone. The app then terminates its queued transactions itself."""

    one_shot: bool = True
    _armed_by_update: bool = field(default=False, init=False)

    def on_frame(self, frame: Frame) -> Action:
        if not frame.from_client or frame.kind != "request":
            return Action.PASS
        if frame.name == "update":
            self._armed_by_update = True
            return Action.PASS
        if frame.name == "ice_trickle" and self._armed_by_update:
            if (frame.body or {}).get("candidate") is None:
                return Action.PASS
            self.fired = True
            return Action.CLOSE
        return Action.PASS


@rule("blackhole")
@dataclass
class Blackhole(Rule):
    """A silent loss of the signaling: every frame both ways is dropped, nothing is closed. The app
    sees no error; requests wait for their answers until the rule is lifted."""

    def on_frame(self, frame: Frame) -> Action:
        return Action.DROP


@rule("close-now")
@dataclass
class CloseNow(Rule):
    """Close every signaling connection on its next frame, either way."""

    one_shot: bool = True

    def on_frame(self, frame: Frame) -> Action:
        self.fired = True
        return Action.CLOSE


class Rules:
    """The rules armed right now, applied in the order they were armed."""

    def __init__(self) -> None:
        self._armed: list[Rule] = []

    @property
    def armed(self) -> list[str]:
        return [r.name for r in self._armed]

    def describe(self) -> list[dict[str, Any]]:
        return [r.describe() for r in self._armed]

    def arm(self, name: str, params: dict[str, str] | None = None) -> Rule:
        rule = build(name, _params_alias(params))
        self._armed.append(rule)
        return rule

    def lift(self, name: str | None = None) -> None:
        self._armed = [r for r in self._armed if name is not None and r.name != name]

    def on_frame(self, frame: Frame) -> tuple[Action, str | None]:
        """What to do with `frame`, and the name of the rule that decided it (None for pass)."""
        for rule in list(self._armed):
            action = rule.on_frame(frame)
            if action is Action.PASS:
                continue
            if rule.one_shot and rule.fired:
                self._armed.remove(rule)
            return action, rule.name
        return Action.PASS, None

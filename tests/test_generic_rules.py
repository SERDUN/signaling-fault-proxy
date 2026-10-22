import json
from pathlib import Path

import pytest

from signaling_fault_proxy.rules import RULES, Action, Frame, Rules, build


def app(request: str, tx: str = "t", **fields) -> Frame:
    return Frame.parse(True, json.dumps({"request": request, "transaction": tx, **fields}))


def core(kind: str, name: str, tx: str = "t") -> Frame:
    return Frame.parse(False, json.dumps({kind: name, "transaction": tx}))


def test_close_on_a_request_after_another():
    rules = Rules()
    rules.arm("close-on", {"from": "app", "name": "hold", "after": "update"})

    assert rules.on_frame(app("hold"))[0] is Action.PASS, "not before an update"
    assert rules.on_frame(app("update"))[0] is Action.PASS
    assert rules.on_frame(core("response", "ack"))[0] is Action.PASS
    assert rules.on_frame(app("hold")) == (Action.CLOSE, "close-on")
    assert rules.armed == [], "times=1 by default: lifts itself"


def test_drop_on_until_lifted():
    rules = Rules()
    rules.arm("drop-on", {"from": "core", "kind": "response", "times": "0"})

    for _ in range(3):
        assert rules.on_frame(core("response", "ack"))[0] is Action.DROP
    assert rules.on_frame(core("event", "accepted"))[0] is Action.PASS
    assert rules.armed == ["drop-on"]


def test_drop_on_a_number_of_times():
    rules = Rules()
    rules.arm("drop-on", {"name": "keepalive", "times": "2"})
    assert rules.on_frame(core("handshake", "keepalive"))[0] is Action.DROP
    assert rules.on_frame(core("handshake", "keepalive"))[0] is Action.DROP
    assert rules.on_frame(core("handshake", "keepalive"))[0] is Action.PASS


def test_parameters_are_checked():
    with pytest.raises(ValueError, match="has no parameter 'nope'"):
        build("close-on", {"nope": "1"})
    with pytest.raises(ValueError, match="invalid literal"):
        build("close-on", {"times": "many"})


def test_a_rule_describes_its_parameters():
    rules = Rules()
    rules.arm("close-on", {"name": "hold"})
    assert rules.describe() == [
        {
            "rule": "close-on",
            "one_shot": True,
            "from": "",
            "kind": "",
            "name": "hold",
            "after": "",
            "times": 1,
        }
    ]


def test_a_rules_file_registers_its_rules():
    from signaling_fault_proxy.addon import _load_rules

    _load_rules(Path(__file__).parent.parent / "examples" / "drop_acks.py")
    assert "drop-acks-for" in RULES

    rules = Rules()
    rules.arm("drop-acks-for", {"request": "hold"})
    assert rules.on_frame(app("hold", tx="h1"))[0] is Action.PASS
    assert rules.on_frame(core("response", "ack", tx="h1"))[0] is Action.DROP
    assert rules.on_frame(core("response", "ack", tx="other"))[0] is Action.PASS

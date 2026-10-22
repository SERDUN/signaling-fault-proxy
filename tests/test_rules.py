import json

import pytest

from signaling_fault_proxy.rules import Action, Frame, Rules


def app(request: str, **fields) -> Frame:
    return Frame.parse(True, json.dumps({"request": request, "transaction": "t", **fields}))


def core(kind: str, name: str) -> Frame:
    return Frame.parse(False, json.dumps({kind: name, "transaction": "t"}))


def test_frames_are_read_by_kind():
    assert app("update").kind == "request"
    assert app("update").name == "update"
    assert core("response", "ack").from_client is False
    assert Frame.parse(True, "not json").kind is None


def test_close_trickle_after_update_fires_once_on_the_first_candidate():
    rules = Rules()
    rules.arm("close-trickle-after-update")

    assert rules.on_frame(app("ice_trickle", candidate={"candidate": "a"}))[0] is Action.PASS
    assert rules.on_frame(app("update"))[0] is Action.PASS
    assert rules.on_frame(core("response", "ack"))[0] is Action.PASS
    assert rules.on_frame(app("ice_trickle", candidate=None))[0] is Action.PASS, "end of candidates"
    assert rules.on_frame(app("ice_trickle", candidate={"candidate": "a"})) == (
        Action.CLOSE,
        "close-trickle-after-update",
    )
    assert rules.armed == [], "a one-shot rule lifts itself"
    assert rules.on_frame(app("ice_trickle", candidate={"candidate": "b"}))[0] is Action.PASS


def test_blackhole_drops_both_ways_until_lifted():
    rules = Rules()
    rules.arm("blackhole")
    assert rules.on_frame(app("hangup"))[0] is Action.DROP
    assert rules.on_frame(core("event", "hangup"))[0] is Action.DROP
    rules.lift("blackhole")
    assert rules.on_frame(app("hangup"))[0] is Action.PASS


def test_lift_without_a_name_lifts_everything():
    rules = Rules()
    rules.arm("blackhole")
    rules.arm("close-now")
    rules.lift()
    assert rules.armed == []


def test_unknown_rule_is_refused():
    with pytest.raises(ValueError, match="unknown rule"):
        Rules().arm("nope")


def test_rules_are_asked_in_arming_order():
    rules = Rules()
    rules.arm("blackhole")
    rules.arm("close-now")
    assert rules.on_frame(app("hangup")) == (Action.DROP, "blackhole")


def test_a_frame_describes_itself():
    assert app("update").describe() == "app -> core request:update tx=t"
    assert core("response", "ack").describe() == "core -> app response:ack tx=t"

import json

import pytest
from mitmproxy.http import Request

from signaling_fault_proxy.addon import SignalingFaultProxy


def test_set_core_valid_and_status():
    p = SignalingFaultProxy()
    p._set_core("https://core.example.com/")
    assert p._upstream == "https://core.example.com"
    st = p._status()
    assert st["core"] == "https://core.example.com"
    assert st["signup_as_login"] is None


def test_set_core_rejects_non_http():
    p = SignalingFaultProxy()
    with pytest.raises(ValueError):
        p._set_core("ftp://nope")


def test_set_signup_and_clear():
    p = SignalingFaultProxy()
    p._set_signup("alice:pw")
    assert p._signup_login == ("alice", "pw")
    assert p._status()["signup_as_login"] == "alice"  # login only
    p._set_signup("")
    assert p._signup_login is None


def test_status_never_exposes_the_password():
    p = SignalingFaultProxy()
    p._set_signup("acc:supersecret")
    assert "supersecret" not in json.dumps(p._status())


def test_route_sends_to_the_configured_core():
    p = SignalingFaultProxy()
    p._set_core("https://core.example.com")
    req = Request.make("GET", "http://127.0.0.1:8080/api/v1/system-info")
    p._route(req)
    assert (req.scheme, req.host, req.port) == ("https", "core.example.com", 443)


def test_set_mock_valid_and_status():
    p = SignalingFaultProxy()
    p._set_mock((200, '{"otp_id": "x"}'))
    assert p._signup_mock == (200, '{"otp_id": "x"}')
    assert p._status()["signup_mock"] == {"status": 200, "body": '{"otp_id": "x"}'}
    p._set_mock(None)
    assert p._signup_mock is None


def test_set_mock_rejects_non_json():
    p = SignalingFaultProxy()
    with pytest.raises(ValueError):
        p._set_mock((200, "not json"))


def test_is_signup_post():
    p = SignalingFaultProxy()
    assert p._is_signup_post(Request.make("POST", "http://h/api/v1/user"))
    assert p._is_signup_post(Request.make("POST", "http://h/tenant/t/api/v1/user?x=1"))
    assert not p._is_signup_post(Request.make("GET", "http://h/api/v1/user"))
    assert not p._is_signup_post(Request.make("POST", "http://h/api/v1/session"))


def test_otp_mock_replays_the_login_on_verify():
    p = SignalingFaultProxy()
    p._set_mock((200, '{"otp_id": "x"}'))
    signup = Request.make(
        "POST",
        "http://h/api/v1/user",
        json.dumps({"identifier": "d", "type": "mobile", "login": "acc", "password": "pw"}),
        {"content-type": "application/json"},
    )
    assert p._mock_response(signup) is not None  # the signup gets the otp mock
    assert p._mock_pending_login == {"identifier": "d", "type": "mobile", "login": "acc", "password": "pw"}
    verify = Request.make(
        "POST", "http://h/api/v1/session/otp-verify", json.dumps({"otp_id": "x", "code": "1"}), {"content-type": "application/json"}
    )
    assert p._mock_response(verify) is None  # not answered - forwarded as a real login
    assert verify.path == "/api/v1/session"
    assert json.loads(verify.get_text()) == {"identifier": "d", "type": "mobile", "login": "acc", "password": "pw"}


def test_non_otp_mock_leaves_verify_alone():
    p = SignalingFaultProxy()
    p._set_mock((200, '{"token": "t"}'))
    verify = Request.make("POST", "http://h/api/v1/session/otp-verify")
    assert p._mock_response(verify) is None


def test_route_http_core_uses_port_80():
    p = SignalingFaultProxy()
    p._set_core("http://core.local:4000")
    req = Request.make("GET", "http://127.0.0.1:8080/x")
    p._route(req)
    assert (req.scheme, req.host, req.port) == ("http", "core.local", 4000)

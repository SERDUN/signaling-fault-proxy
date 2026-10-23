import json

from mitmproxy.http import Request

from signaling_fault_proxy.addon import SignalingFaultProxy


def signup_request(url: str, body: dict) -> Request:
    return Request.make("POST", url, json.dumps(body), {"content-type": "application/json"})


def rewrite(url: str, body: dict, creds=("testuser", "secret")) -> Request:
    proxy = SignalingFaultProxy()
    proxy._signup_login = creds
    req = signup_request(url, body)
    proxy._signup_as_login(req)
    return req


def test_signup_becomes_login_keeping_device_identity():
    req = rewrite(
        "http://127.0.0.1:8080/api/v1/user",
        {"identifier": "dev-uuid", "type": "mobile", "bundle_id": "com.x", "email": "a@b.c"},
    )
    assert req.path == "/api/v1/session"
    assert json.loads(req.get_text()) == {
        "identifier": "dev-uuid",
        "type": "mobile",
        "bundle_id": "com.x",
        "login": "testuser",
        "password": "secret",
    }


def test_body_credentials_override_the_default():
    req = rewrite(
        "http://127.0.0.1:8080/api/v1/user",
        {"identifier": "dev-uuid", "type": "mobile", "email": "a@b.c", "login": "alice", "password": "pw"},
    )
    assert req.path == "/api/v1/session"
    assert json.loads(req.get_text()) == {
        "identifier": "dev-uuid",
        "type": "mobile",
        "login": "alice",
        "password": "pw",
    }


def test_empty_login_falls_back_to_the_default():
    req = rewrite(
        "http://127.0.0.1:8080/api/v1/user",
        {"identifier": "dev-uuid", "type": "mobile", "login": "", "password": ""},
    )
    body = json.loads(req.get_text())
    assert (body["login"], body["password"]) == ("testuser", "secret")


def test_body_credentials_work_with_no_default():
    req = rewrite(
        "http://127.0.0.1:8080/api/v1/user",
        {"identifier": "dev-uuid", "type": "mobile", "login": "bob", "password": "pw"},
        creds=None,
    )
    assert req.path == "/api/v1/session"
    assert json.loads(req.get_text()) == {"identifier": "dev-uuid", "type": "mobile", "login": "bob", "password": "pw"}


def test_no_default_and_no_body_login_is_left_untouched():
    body = {"identifier": "dev-uuid", "type": "mobile", "email": "a@b.c"}
    req = rewrite("http://127.0.0.1:8080/api/v1/user", dict(body), creds=None)
    assert req.path == "/api/v1/user"  # not rewritten
    assert json.loads(req.get_text()) == body  # unchanged


def test_rewrite_off_sends_the_signup_to_core_untouched():
    proxy = SignalingFaultProxy()
    proxy._signup_login = ("acc", "pw")
    proxy._rewrite_signup = False
    req = signup_request(
        "http://127.0.0.1:8080/api/v1/user", {"identifier": "d", "type": "mobile", "login": "x", "password": "y"}
    )
    proxy._signup_as_login(req)
    assert req.path == "/api/v1/user"  # not rewritten - the Core handles the signup


def test_tenant_prefix_is_kept():
    req = rewrite(
        "http://127.0.0.1:8080/tenant/t-1/api/v1/user",
        {"identifier": "dev-uuid", "type": "mobile"},
    )
    assert req.path == "/tenant/t-1/api/v1/session"


def test_query_string_is_kept():
    req = rewrite("http://127.0.0.1:8080/api/v1/user?x=1", {"identifier": "d", "type": "mobile"})
    assert req.path == "/api/v1/session?x=1"


def test_other_posts_are_left_alone():
    proxy = SignalingFaultProxy()
    proxy._signup_login = ("testuser", "secret")
    req = signup_request("http://127.0.0.1:8080/api/v1/session", {"login": "real", "password": "p"})
    proxy._signup_as_login(req)
    assert req.path == "/api/v1/session"
    assert json.loads(req.get_text()) == {"login": "real", "password": "p"}


def test_get_on_user_is_left_alone():
    proxy = SignalingFaultProxy()
    proxy._signup_login = ("testuser", "secret")
    req = Request.make("GET", "http://127.0.0.1:8080/api/v1/user/info")
    proxy._signup_as_login(req)
    assert req.path == "/api/v1/user/info"

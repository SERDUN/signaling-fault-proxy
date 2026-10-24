"""The vendor page's mock backend answers the page's login flow the way the real backend does."""

from __future__ import annotations

import importlib.util
import json
import pathlib
import threading
import urllib.error
import urllib.request

import pytest

HERE = pathlib.Path(__file__).resolve().parent.parent / "testbench" / "vendor-spa"
spec = importlib.util.spec_from_file_location("vendor_spa_mock", HERE / "mock_backend.py")
mock = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mock)


@pytest.fixture()
def base():
    server = mock.make_server("127.0.0.1", 0, HERE / "accounts.json", quiet=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def call(base: str, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        base + path, data=data, method=method, headers={"content-type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_sign_in_with_the_code_gives_portabilling_shaped_tokens(base):
    status, signin = call(base, "POST", "/api/v1/sign-in", {"msisdn": "27710000001"})
    assert status == 200 and set(signin) == {"reg_token", "otp"} and signin["otp"] is None

    status, wrong = call(
        base, "POST", "/api/v1/otp/verify", {"reg_token": signin["reg_token"], "otp": "000000"}
    )
    assert status == 400 and wrong["class"] == "DemoVendorWrongOTP"

    status, tokens = call(
        base, "POST", "/api/v1/otp/verify", {"reg_token": signin["reg_token"], "otp": "123456"}
    )
    assert status == 200 and set(tokens) == {"access_token", "refresh_token"}
    assert tokens["access_token"].count(".") == 2

    auth = {
        "auth": {
            "access_token": tokens["access_token"],
            "refresh_token": tokens["refresh_token"],
            "expires_at": None,
        }
    }
    status, home = call(base, "POST", "/api/v1/home-info", auth)
    assert status == 200 and home["msisdn"] == "27710000001" and home["version"] == "1.4.2"


def test_unknown_number_is_not_registered_and_can_register(base):
    status, err = call(base, "POST", "/api/v1/sign-in", {"msisdn": "27710000099"})
    assert status == 400 and err["class"] == "DemoVendorNotRegistered"

    status, signup = call(base, "POST", "/api/v1/sign-up", {"msisdn": "27710000099", "first_name": "New"})
    assert status == 200
    status, _ = call(base, "POST", "/api/v1/otp/verify", {"reg_token": signup["reg_token"], "otp": "123456"})
    assert status == 200
    status, _ = call(base, "POST", "/api/v1/sign-in", {"msisdn": "27710000099"})
    assert status == 200


def test_logged_in_calls_need_a_session(base):
    status, err = call(base, "POST", "/api/v1/home-info", {"auth": {"access_token": "nope"}})
    assert status == 401 and err["class"] == "DemoVendorInvalidToken"


def test_the_page_routes_fall_back_to_the_app_shell(base):
    with urllib.request.urlopen(base + "/verify") as r:
        page = r.read().decode()
    assert "The Demo Vendor App" in page and "/assets/index-" in page
    status, config = call(base, "GET", "/api/v1/sign-up/config?ui_version=1.4.2")
    assert status == 200 and config["version"] == "1.4.2" and "ZA" in config["countries"]

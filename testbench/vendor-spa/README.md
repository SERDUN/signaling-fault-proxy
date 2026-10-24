# Vendor login page with a mock backend

A real vendor's embedded login page, run locally against a mock of its backend, so the app's
embedded login can be tested and debugged without the vendor's production.

- `site/` - the vendor's production single-page app (React, one Vite bundle) copied as it was
  served on 2026-10-24 (UI version 1.4.2) and **rebranded**: the product, operator and domain names,
  the logo, the favicon, the support address and the payment provider key are replaced
  ("Demo Vendor", "Operator S" / "Operator Z", `example.com`). Nothing else in the bundle is changed.
- `mock_backend.py` - one stdlib server that serves `site/` (every non-file path falls back to
  `index.html`, as the page routes on the client) and plays its backend under `/api/v1`.
- `accounts.json` - synthetic accounts, the SMS code, bundles, friends.

How this page was made, and how to add another one the same way: [docs/vendor-pages.md](../../docs/vendor-pages.md).
It is used by [embedded page stuck after a device limit](../../docs/reproduce/embedded-page-stuck-after-device-limit.md).

The page reads its API from `window.location.origin + "/api/v1"`, so it only works when served by
`mock_backend.py`. The copy GitHub Pages publishes under `testbench/vendor-spa/site/` has no backend
and is not meant to be opened.

## Run

```sh
python3 testbench/vendor-spa/mock_backend.py --port 8415
# open http://127.0.0.1:8415/auth
```

| Account (`accounts.json`) | What it is |
|---|---|
| `+27 71 000 0001` | registered, user class `OPS`, no subscription, one friend |
| `+27 71 000 0002` | registered, user class `OPZ`, an active subscription and one history record |
| any other valid number | not registered: **Log in** says so, **Register** creates it |

The SMS code is `123456` for every number. State (sign-ups, subscriptions, friends) lives in memory
until the server stops.

The logged-in screens (`/home` and what it links to) get their session from the app, which calls
`window.onPayloadDataReady({externalPageToken: {...}})`. In a plain browser,
`http://127.0.0.1:8415/home?dev_payload=27710000001` does the same for that account.

## In the app

The page is a launch login page: an embedded resource whose id is
`loginConfig.common.fullScreenLaunchEmbeddedResourceId`.

```json
{ "id": "vendor_spa", "uri": "http://127.0.0.1:8415/auth", "payload": ["externalPageToken"], "toolbar": { "showToolbar": false } }
```

```sh
adb reverse tcp:8415 tcp:8415      # the page on the phone's 127.0.0.1
```

After the code is verified the page posts `{event: "signup", data: {access_token, refresh_token}}`
to the app (sign-in), or opens `/complete` and posts it there one second later with a `callback`
to `/api/v1/welcome` (registration). The app sends `data` to its Core as `POST /api/v1/user`. On a
stand without the vendor's signup backend, put the proxy in front of the Core with a default
account, so that signup becomes a login:

```sh
SFP_UPSTREAM=http://<stand>:4000 SFP_SIGNUP_AS_LOGIN=<number>:<password> \
  uv run signaling-fault-proxy --port 8411
```

## App contract

Read from the bundle; worth knowing before changing how the app treats an embedded page.

| Contract | What the page does |
|---|---|
| `WebtritLoginChannel` | posts `signup` with the vendor's tokens (PortaBilling JWT + refresh token); defines a console stub when the channel is missing |
| `WebtritPageReloadChannel` | asks the app to reload it when its API version and UI version differ (`/sign-up/config`) |
| `window.onDeviceInfoReady` | waits up to 3 s for the app's device info before it shows anything |
| `window.onPayloadDataReady` | `/home` takes `externalPageToken` from the app's payload as its session |
| result of `signup` | none: the page never learns how the login ended. On sign-in it keeps a full-screen loader on `/verify`; on registration it sits on `/complete` |
| history | a single-page app: every screen is a `history.pushState` route, its data in `history.state` |

`/complete` posts `signup` one second after it is shown, from the tokens in `history.state`, and
`history.state` survives a reload. So **a reload of `/complete` sends the signup again**: an app that
answers a refused signup by reloading the page can loop; going back in the page's history
(`/complete` -> `/verify`) does not.

## Fidelity

| Endpoint | Source of the mock answer |
|---|---|
| `GET /sign-up/config`, `POST /sign-in`, `POST /otp/verify`, `POST /home-info`, `POST /subscriptions` | recorded from the production backend with a test account (shapes; values synthetic) |
| `POST /sign-up`, `/otp/resend`, `/info/*`, `/invite*`, `/friends/*`, `/subscribe*`, `/external-login`, `/welcome` | built from what the page sends and reads; not seen on production |
| `/subscribe/stripe/*` | not mocked: answers `DemoVendorNotAllowed` |

Errors come back as `{class, message, code, details}`; the page picks its message by `class`
(`DemoVendorWrongOTP`, `DemoVendorNotRegistered`, `DemoVendorAlreadyRegistered`,
`DemoVendorInvalidMSISDN`, `DemoVendorInvalidToken`, ...). Status 590-599 sends the page to its
maintenance screen.

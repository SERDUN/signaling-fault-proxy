# Serving a custom signup as a login

## The missing signup backend

The PortaPhone app can show a **custom signup** page - an embedded web page in a `WebView` that
collects whatever a deployment wants (an email, a clinic, a tenant) and hands it back to the app,
which then calls `POST /api/v1/user` on the Core. In production that endpoint runs a real signup and
answers with the session the app logs in with.

On a test stand there is often **no custom-signup backend** behind that endpoint, so the signup
screen cannot be exercised end to end: the page submits, `POST /api/v1/user` fails, and the app never
enters a session. You cannot test the screen that most needs testing.

## The rewrite: signup as login

`--signup-as-login LOGIN:PASSWORD` (or the `SFP_SIGNUP_AS_LOGIN` environment variable) turns that
signup into an ordinary login, for test accounts, without touching the app or the Core:

```
 app            signaling-fault-proxy                     Core
  |  POST /api/v1/user  {identifier,type,bundle_id, ...form}
  | ------------------------>|
  |                          |  rewrite: keep identifier/type/bundle_id,
  |                          |  drop the form fields, add login+password
  |                          |  POST /api/v1/session {identifier,type,bundle_id,login,password}
  |                          | --------------------------------------> |
  |                          |                           200 {token,...}|
  |      200 {token, ...} <--|<-------------------------------------- |
```

Both endpoints answer with the same `SessionToken` (`{token, user_id?, tenant_id?}`), and the app's
api client parses the `POST /api/v1/user` response with that same shape, so the login comes back as if
the signup had returned it. The device identity (`identifier`, `type`, `bundle_id`) is the one the app
already sent, so device-limit handling stays consistent; a `/tenant/<id>/...` prefix is kept, so the
tenant stays the same.

If the signup body itself carries a non-empty `login` (and `password`), those win and the proxy logs
in as that account; an empty or absent `login` falls back to the configured one. So a signup page can
offer optional login/password fields: left blank they use the proxy's account, filled they pick one.

The rewrite is scoped to `POST` on a path ending `/api/v1/user`. Everything else - including the
`GET /api/v1/system-info` the app fetches right after login, and all the authenticated calls after
that - passes through to the Core untouched. So the Core still has to be a working one for the session
to come up; the stub only stands in for the one missing signup backend.

## Core and credentials from the environment

Nothing is hardcoded and no secret belongs in the repo:

| variable | meaning |
|---|---|
| `SFP_UPSTREAM` | the Core to forward to (same as `--upstream`) |
| `SFP_SIGNUP_AS_LOGIN` | `LOGIN:PASSWORD` the signup logs in as (same as `--signup-as-login`) |

```sh
SFP_UPSTREAM=https://core.example.com \
SFP_SIGNUP_AS_LOGIN=myaccount:mypassword \
uv run signaling-fault-proxy --port 8411
```

A command-line flag, when given, wins over the variable. Copy [`.env.example`](../.env.example) to
`.env` (gitignored) to keep them out of your shell history.

## The embedded page on login in the real app

The embedded signup page is **not** advertised by the Core - it is the app's own build config. Two
bundled files in the app decide whether it opens on login:

- `assets/themes/app.config.json` - set `loginConfig.common.fullScreenLaunchEmbeddedResourceId` to a
  resource id (it is `null` by default, which gives the native login screen);
- `assets/themes/app.embedded.config.json` - add that resource with a `uri` pointing at the signup
  page (a hosted URL, or a local one you serve - see the bench below).

Build the app with its Core set to the proxy (`WEBTRIT_APP_CORE_URL`, or pick it on the login screen),
put the phone's `127.0.0.1` onto the proxy with `adb reverse tcp:8411 tcp:8411`, and the signup page's
`POST /api/v1/user` lands on the proxy, which turns it into a login.

## The bench

[`testbench/`](../testbench) runs the whole loop with a browser standing in for the app, and - unless
you point it at a real Core - an offline stub Core, so it needs nothing but this repo and no
credentials. See [testbench/README.md](../testbench/README.md).

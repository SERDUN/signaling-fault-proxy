# Embedded login page stuck after a device-limit refusal

## Bug

The app can open a customer's web page as its launch login screen. The page collects the user's
data, puts a spinner on its own Submit button and posts `signup` to the app; it is never told how
the attempt ended. The app resets the page (back in its history, or a reload) only from the error
screen's Back button for a failed request.

Every other way a signup ends without a session leaves the page as it was - spinner on, button
disabled - until the app is restarted:

| Core's answer | What the app shows | When the page is stuck |
|---|---|---|
| 409 `device_limit_reached` with sessions | the device-limit screen over the page | after Back on that screen (arrow or system key) |
| 409 `device_limit_reached` without sessions | a message only | at once |
| 403 `device_switch_limit_reached` | a message only | at once |
| 500 and the like | the error screen | after the **system** Back key on it |
| no connection | the error screen, Retry only | after the system Back key |

A one-time code (`{"otp_id": ...}`) does not stick: the code screen is on a route that does not keep
the page, so the page is built again when the user comes back - by accident, not by design.

On a real stand the first row needs an account at its device limit; the proxy makes every row on
demand.

## Fault

The proxy answers the app's `POST /api/v1/user` with a canned response (`/__proxy/mock`), so Core's
refusals come without configuring limits on the stand. The real device limit gives the same 409; it
was checked once with the stand's Core and behaves the same.

## Setup

- an Android phone on USB, the stand's Core, this repository;
- a debug build of the app with its Core set to the proxy and a launch login page:
  - `WEBTRIT_APP_CORE_URL=http://127.0.0.1:8413` (`--dart-define-from-file`);
  - `assets/themes/app.embedded.config.json`: add
    `{"id": "wt_page", "uri": "<page>", "toolbar": {"showToolbar": false}}`;
  - `assets/themes/app.config.json`: `loginConfig.common.fullScreenLaunchEmbeddedResourceId: "wt_page"`;
- the page, one of:
  - `https://serdun.github.io/signaling-fault-proxy/testbench/embedded/custom_signup_tabs.html`
    (our form page; **Custom signup** takes a login and password, which the proxy logs in as);
  - the vendor page: `python3 testbench/vendor-spa/mock_backend.py --port 8415`, page
    `http://127.0.0.1:8415/auth`, `adb reverse tcp:8415 tcp:8415`
    (see [vendor pages](../vendor-pages.md));
- the proxy:

  ```sh
  uv run signaling-fault-proxy --upstream http://<stand>:4000 --port 8413
  adb reverse tcp:8413 tcp:8413
  ```

## Steps

1. Arm Core's answer, for example the device limit with one session to pick:

   ```sh
   curl -G http://127.0.0.1:8413/__proxy/mock --data-urlencode status=409 \
     --data-urlencode 'body={"code":"device_limit_reached","sessions":[{"id":"s1","status":"active","current":false,"app_type":"android","app_identifier":"other-device","created_at":"2026-01-01T00:00:00Z"}],"pending_login_token":"t"}'
   ```

   Other rows: `status=409 body={"code":"device_limit_reached"}`,
   `status=403 body={"code":"device_switch_limit_reached"}`, `status=500 body={"code":"internal_error"}`;
   for "no connection" drop the reverse after the page has loaded (`adb reverse --remove tcp:8413`).
2. Open the app, fill the page, submit.
3. Leave the way the row says (Back arrow, system Back key, or nothing).
4. Wait 15 s and look at the page; tap its Submit.
5. `curl 'http://127.0.0.1:8413/__proxy/mock?off=1'`.

The scripts the runs below were made with (they drive the form page by its accessibility tree and
judge the page from it) are kept with the task, not here.

## Expected

**The proxy**: one `POST /api/v1/user` answered with the canned status.

**A build with the bug**: the page is on its form with the spinner; the button sends nothing. The
app's log has no `WebViewContainer` line after the refusal - the page was neither reloaded nor sent
back.

**A fixed build**: the page is back in a usable state - reloaded or back in its history, or told
the result if it takes one - and Submit sends a new request.

**The vendor page** (`/complete` posts `signup` again whenever it is shown, from `history.state`,
which survives a reload): with a fix that goes back in the page's history it returns to `/verify`
and posts nothing more; a fix that **reloads** it would make it post again, get refused again and
loop.

## Seen

2026-10-24, app master `f85f3997dea` (debug), Redmi 9A (Android 10) and Galaxy XCover 5 (Android 14):

| Build | Device limit, Back arrow | Device limit, system Back | Device limit, real Core | No sessions | 403 | 500, system Back | No connection |
|---|---|---|---|---|---|---|---|
| master (before and after the fixed build) | stuck 4/4 | stuck 3/3 | stuck 2/2 | stuck 3/3 | stuck 4/4 | stuck 4/4 | stuck 4/4 |
| reset the page on every end without a session | reset 3/3 | reset 3/3 | reset 2/2 | reset 3/3 | reset 3/3 | reset 3/3 | reset 3/3 |

The 500 with the error screen's own Back arrow is reset on master already (3/3) - the one path the
app handles today.

Vendor page with that reset (history back): back on `/verify`, one request, 3/3. Reloading the
vendor page on `/complete` made it post `signup` again 2/2.

## Notes

- The form page keeps one Submit button for all its methods; its own "Back" link hides it without
  clearing the spinner, so only the app can make it usable again.
- A page that defines `window.onSignupResult(result)` could clear its own state if the app called
  it (WebTrit's example page already does); the app does not call it today.

# Vendor pages: real embedded pages with a mock backend

The app shows pages that customers write themselves: an embedded login page, a tab, a balance
screen. Our own test pages (`testbench/embedded/`) show what the contract allows; a customer's page
shows what is really out there - a single-page app with its own router, its own backend, timers,
retries and assumptions about what the app does. A change of the app that is fine with our pages
can break theirs.

A **vendor page** here is such a production page, copied once, rebranded, and served together with a
mock of its backend, so it can be run against any stand, as often as needed, without touching the
vendor's production again.

| Page | What it is | Used by |
|---|---|---|
| [testbench/vendor-spa/](../testbench/vendor-spa/README.md) | React login + account app (phone number, SMS code, PortaBilling tokens); launch login page and a logged-in tab | [embedded page stuck after a device limit](reproduce/embedded-page-stuck-after-device-limit.md) |

## Layout of one page

```
testbench/<name>/
  site/             the copied site, rebranded - nothing else changed
  mock_backend.py   serves site/ (client routes fall back to index.html) and its backend, one origin
  accounts.json     synthetic accounts, codes and data the mock answers with
  README.md         run it, accounts, wiring into the app, app contract, fidelity
tests/test_<name>_mock.py
```

One origin for the page and its backend matters: such pages usually call
`window.location.origin + "/api/..."`, so the page only works when the mock serves it. GitHub
Pages publishes `site/` too, but without a backend that copy is not usable.

## Adding a page

### 1. Read the bundle first

Download the page and its bundle into a scratch directory (never into the repo yet) and find where
it meets the app - before clicking anything:

```sh
grep -o 'Webtrit[A-Za-z]*Channel\|window\.on[A-Z][A-Za-z]*\|onWebTritResult\|onSignupResult\|initializePage' bundle.js | sort | uniq -c
```

Write down, for the page's README ("App contract"): what it posts on which channel and when; which
`window.on...` functions it defines and what it expects in them; what it shows while waiting for the
app and whether it ever stops waiting; whether it is a single-page app (`history.pushState`, state in
`history.state`) and what it does again when it is reloaded. List its API calls (`grep` for the
client function, usually one `request(method, path, body)` helper) - that is the mock's to-do list.

### 2. Capture production once, with a test account

The vendor gives a test account. Walk the flows once in a desktop browser with the console open:
such pages often log every request and answer (`callApi request` / `callApi response`); otherwise
use the network panel. Record **shapes and error classes**, not values:

- mask every token before the log leaves the browser session; never commit a token, a real number
  or the test account's code;
- go only through reads (sign-in, the account screens); leave out payments, invites, anything that
  sends SMS to someone else or changes data;
- close the browser when done - from here on the mock answers.

### 3. Copy and rebrand

Copy `index.html`, the bundle, its CSS and every file the bundle references by path
(`grep -o '"/[A-Za-z0-9_./-]*\.\(svg\|png\|jpg\|webp\|ico\|woff2\?\)"' bundle.js`).

Then rebrand with [`scripts/rebrand_site.py`](../scripts/rebrand_site.py) and a **private** map (it
names the vendor, so it lives in the private task workspace, never here):

```sh
python3 scripts/rebrand_site.py /tmp/copied-site testbench/<name>/site --map <private>/rebrand-map.json
```

What to put in the map:

- product names in every spelling (`The X App`, `X`, `XApp`, `xapp`, `X_APP`), longest first;
- domains and URLs (terms, privacy, support, store links) -> `example.com`;
- operator and partner names, and the **codes** derived from them (`user_class` values, provider
  ids, error class names) - rename them consistently, the mock answers with the new ones;
- e-mail addresses, analytics ids, payment provider keys (`pk_live_` / `pk_test_`) -> placeholders;
- logos and favicons -> neutral files under `"files"`;
- `"forbid"`: every original word; the script fails while one is left.

Leave alone what the page computes with: country names, number prefixes it validates, versions.
Check the result with the same `forbid` list over the whole staged tree before committing.

### 4. Write the mock

Start from [`testbench/vendor-spa/mock_backend.py`](../testbench/vendor-spa/mock_backend.py): stdlib
only, one `ThreadingHTTPServer`, static files with a client-route fallback, `/api/...` dispatched to
methods of an in-memory backend loaded from `accounts.json`.

- Answer the recorded endpoints with the recorded shapes and the vendor's error format; mark the
  rest as inferred in the README's "Fidelity" table.
- Synthetic accounts must pass the page's own validation (a real-looking number format), and cover
  the cases the page branches on (registered / not, with and without a subscription, each user
  class).
- Make the page usable in a plain browser too: where the app injects a session
  (`onPayloadDataReady`), offer a query switch that does the same (`?dev_payload=<account>`).
- Add `tests/test_<name>_mock.py`: the main flow end to end over HTTP, one error per class the page
  reacts to, the client-route fallback.

## Running a page with the app

```
 phone (app)  --WebView-->  mock_backend.py :8415   (page + its backend)        adb reverse tcp:8415
      |
      +--------- Core API --> signaling-fault-proxy :8411 --> stand Core         adb reverse tcp:8411
```

1. Start the mock: `python3 testbench/<name>/mock_backend.py --port 8415`.
2. Start the proxy in front of the stand's Core. A vendor page signs up with its own tokens, not a
   login, so give the proxy a default account (`SFP_SIGNUP_AS_LOGIN`) - see
   [signup as login](signup-as-login.md).
3. Build the app with its Core set to the proxy (`WEBTRIT_APP_CORE_URL=http://127.0.0.1:8411`) and
   the page as an embedded resource: for a launch login page add it to
   `assets/themes/app.embedded.config.json` and point
   `loginConfig.common.fullScreenLaunchEmbeddedResourceId` at its id.
4. `adb reverse tcp:8415 tcp:8415` and `adb reverse tcp:8411 tcp:8411`, start the app.

Core's answers to the page's signup can then be chosen from the proxy without changing the stand:
`/__proxy/mock?status=409&body=...` for a device limit, `403` for a device-switch limit, `500` for a
failed request, `{"otp_id": ...}` for a one-time code (see the proxy's README, "Control API"). The
mock and the proxy log every request; the app's log (`adb logcat -s flutter:*`) has the other side.

## Keeping a page current

The page checks its own version against its backend (`ui_version` in the request, `version` in the
answer) and asks the app for a reload when they differ. The mock answers with the copied version, so
the copy stays stable. When the vendor ships a new version that matters to a test, repeat steps 1-3
into a new commit; keep the private map next to the task so the next copy is rebranded the same way.

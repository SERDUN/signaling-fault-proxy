# Connecting the app to the proxy

The app has to use the proxy as its Core: every HTTP request and the signaling WebSocket go to the
proxy, which forwards them to the real Core. Accounts, passwords and everything else stay the real
Core's.

## Android (recommended: over USB)

1. Start the proxy on your computer:

   ```sh
   uv run signaling-fault-proxy --upstream https://core.example.com --port 8080 --journal runs/journal.jsonl
   ```

2. Tunnel the port to the phone over USB:

   ```sh
   adb -s <serial> reverse tcp:8080 tcp:8080
   adb -s <serial> reverse --list        # UsbFfs tcp:8080 tcp:8080
   ```

   On the phone `127.0.0.1:8080` is now the proxy. The signaling goes over the cable, so switching the
   phone's Wi-Fi or mobile data changes only the media path - the trick most reproductions rely on.
   Wireless adb works too, but then the tunnel depends on the Wi-Fi you may want to switch off.

3. Make the app use `http://127.0.0.1:8080` as its Core, one of:
   - **sign-in screen** - when the build lets you enter a Core address, enter `http://127.0.0.1:8080`;
   - **build** - build the app with the Core built in and plain HTTP allowed to it:

     ```sh
     flutter build apk --release \
       --dart-define=WEBTRIT_APP_CORE_URL=http://127.0.0.1:8080 \
       --dart-define=WEBTRIT_HTTP_ALLOWED_DOMAINS=127.0.0.1
     ```

     (with the rest of your usual `--dart-define-from-file`). `127.0.0.1` is in the default allowed
     domains of the WebTrit app, so plain HTTP to the proxy is accepted.

4. Sign in. If the app was signed in to the real Core before, sign out first: a session remembers the
   Core it was opened on, and would keep going there directly.

5. Check that the signaling goes through the proxy:

   ```sh
   curl -s http://127.0.0.1:8080/__proxy/status     # "signaling_connections": 1
   ```

## iOS, or Android without USB

There is no `adb reverse`: run the proxy on the LAN and use the computer's address.

```sh
uv run signaling-fault-proxy --upstream https://core.example.com --host 0.0.0.0 --port 8080
```

Use `http://<computer LAN IP>:8080` as the Core (allow plain HTTP to that host in the build). The
signaling then shares the phone's Wi-Fi with the media, so a reproduction cannot switch the Wi-Fi off
without losing the signaling too - pick a rule that does not need it.

## Web

Build or run the web app with the proxy as its Core (`WEBTRIT_APP_CORE_URL=http://127.0.0.1:8080`).
The browser reaches the proxy directly; Core's CORS rules apply as they would to Core itself. A web
build may also need `WEBTRIT_APP_WEB_BUNDLE_ID` set to a bundle id the Core knows, or sign-in fails
with `unconfigured_bundle_id` - that is the Core's answer, not the proxy's.

## Stop

`Ctrl-C` the proxy, then `adb -s <serial> reverse --remove tcp:8080`, and sign the app back in to the
real Core if you keep using it.

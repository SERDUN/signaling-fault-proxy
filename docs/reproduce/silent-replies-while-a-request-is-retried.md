# Silent replies while the app retries a request

## Bug

A call is up. The phone loses its Wi-Fi for a moment, the media path breaks and the app restarts
ICE: it sends `update` with a new offer. If Core's frames stop reaching the app from then on while
the signaling connection stays open, the app retries the `update` every 10 s for as long as that
lasts, the far end does not hear the user, and the app never finds out that the connection is dead.

Each outgoing request put the client's keepalive timer off, so a retried request every 10 s meant no
keepalive was ever sent, and the keepalive was the only thing that could find the dead connection.
A connection that had not got Core's state handshake yet had no keepalive at all. Core does not
close the connection either: every frame from the app restarts its own 60 s timer.

On a real network the same happens when the phone's packets still leave but Core's do not reach it
(a one-way loss); on Android the socket stays open, Core hangs the call up at about 107 s and the
phone still shows it. That loss is hard to make by hand; the proxy makes the signaling half of it on
demand. The iPhone does not show it: its socket goes down by itself about 18 s after the
unanswered request.

The fix watches what the client receives: nothing heard for the keepalive interval sends the
keepalive at once, whatever is being sent meanwhile; no answer in 10 s gives the connection up and
the requests waiting on it end at once. A keepalive answered before the state handshake gives the
connection up too, since Core always sends the state first.

## Fault

`drop-on` with `after=update` drops every frame Core sends once the app's next `update` has passed;
the app's frames still reach Core. The connection is not closed, so only the app can notice.

Two scopes:

- every connection (the default) - also every connection the app makes while the rule is armed
  loses Core's frames, its state handshake included. No build gets the media back before the
  rule is lifted; this shows whether the app notices at all, and how it recovers after;
- `connection=one` - only the connection the `update` went on; a connection the app makes after it
  works. This is the one-way loss of a real network: a build that notices reconnects, replays its
  kept offer and the far end hears the user again while the rule is still armed.

The ICE restart comes from switching the phone's Wi-Fi off for 5 s during the call.

## Setup

- an Android phone with mobile data, on USB (`adb`), with the app connected through the proxy over
  `adb reverse` - see [Connecting the app](../connecting-the-app.md); a build that logs at FINE
  shows the keepalive lines;
- a second party to call it (a SIP phone or another client), so there is audio to lose;
- the proxy running with a journal:

  ```sh
  uv run signaling-fault-proxy --upstream https://core.example.com --port 8080 --journal runs/silent.jsonl
  ```

## Steps

1. Call the phone from the second party, answer, check there is audio both ways. Leave the call
   quiet for half a minute: the app's keepalives go out and are answered (`keepalive` both ways in
   the journal).
2. Switch the phone's Wi-Fi off for 5 s and back on, and arm the rule as soon as the phone is on its
   Wi-Fi again - before the app's next `update`:

   ```sh
   adb -s <serial> shell svc wifi disable; sleep 5; adb -s <serial> shell svc wifi enable
   # every connection
   curl 'http://127.0.0.1:8080/__proxy/arm?rule=drop-on&from=core&after=update&times=0'
   # or only the connection the update goes on
   curl 'http://127.0.0.1:8080/__proxy/arm?rule=drop-on&from=core&after=update&times=0&connection=one'
   ```

3. Wait three minutes. Watch the journal and the app's log; ask the far end whether it hears the
   user.
4. Lift the rule and see whether the audio comes back:

   ```sh
   curl 'http://127.0.0.1:8080/__proxy/lift'
   ```

## Expected

**The proxy**, with any build: the first dropped frame is Core's answer to the `update`, then every
frame Core sends on that connection (and, without `connection=one`, on every later one):

```json
{"t": 1794380844.542, "from_client": false, "kind": "response", "name": "ack", "transaction": "transaction-60002222411", "action": "drop", "rule": "drop-on"}
{"t": 1794380844.543, "from_client": false, "kind": "event", "name": "updating", "transaction": "transaction-60002222411", "action": "drop", "rule": "drop-on"}
```

**A build with the bug**: the `update` again every 10 s, no `keepalive` from the app, no connection
closed or opened for the whole window. Its log shows the keepalive put off after every retry:

```
FINE WebtritSignalingClient - 1 keepalive timer scheduled: 0:00:30.000000
```

The far end does not hear the user until the rule is lifted, with either scope; after the lift the
retried `update` is answered on the same connection and the audio comes back.

**A fixed build**: about 30 s after the last frame it received, the app sends `keepalive`; 10 s later
it gives the connection up (`SignalingKeepaliveTimeoutException`) and connects again 3 s after that.

- `connection=one`: the new connection gets its state, the app replays its kept offer (`replaying the
  pending local offer` in its log) and the far end
  hears the user again some 43 s after the first dropped frame, with the rule still armed.
- every connection: each new connection loses its state too and is given up in turn, either by a
  keepalive with no answer or by one answered before the state
  (`SignalingStateHandshakeMissingException`). After the lift the next connection gets its state,
  the kept offer is replayed and the audio comes back.

## Seen

2026-11-11 and 2026-11-12, an Android 15 phone on USB, the far end a SIP party, the same Core:

| Build | Scope | In the three minutes | After the lift |
|---|---|---|---|
| with the bug (master, and the 1.19.0 release) | every connection | 15-18 `update`, every 10 s; no keepalive, no new connection | audio both ways in 19-30 s |
| with the bug | `connection=one` | 15 `update`; no keepalive; the far end never hears the user | audio in 19 s |
| the watchdog probe of the fix | `connection=one` | keepalive at +30 s, given up at +40 s, new connection at +43 s, offer replayed: the far end hears the user again at about 43 s | - |
| the fix | every connection | keepalive at +30 s, given up at +40 s, a new connection given up every 43 s | the state rule fired; audio both ways in 32-41 s |

The one-way loss on a real network (no proxy) was run as well: the build with the bug lost the call,
the fixed one got the audio back 43-50 s after the loss, in front and in the background.

## Notes

- Arm the rule after the phone is back on its Wi-Fi, not while it is on mobile data: on mobile data
  the app reconnects and sends its `update` from an interface the media may not reach, and an
  earlier `update` (a hold, a re-INVITE) would arm the drop at the wrong moment.
- With a lost answer to one ICE restart and nothing retried, the media server may end the call by
  itself within seconds (its ICE fails); the long silent call needs the `update` the app retries.
- Without `connection=one` the rule drops the state of every connection the app makes. A real
  network does not lose one frame of a live connection for good, so a connection that answers its
  keepalives but never got its state is a property of the rule; the fix gives such a connection up
  because Core always sends the state first.
- After the lift the fix needs up to one keepalive round on its last connection, so with every
  connection dropped it gets the audio back a little later than a build that kept its first
  connection; with `connection=one` only the fix gets it back at all.

# Lost trickle request after an ICE restart ends the call (WT-2071)

## Bug

A call is up. The media path breaks, the app restarts ICE: it sends `update` with a new offer and
trickles its new candidates. If the signaling session goes down while a candidate request is still
waiting for Core's answer, the request fails with `WebtritSignalingTransactionTerminateByDisconnectException`.
[`__onMutationTrickleIce`](https://github.com/SERDUN/work-mirror/blob/mobile-app/master/phone/lib/features/call/bloc/call_bloc.dart)
treated that as a fatal error and ended the call - on its own, while Core still held it.

On real networks the session goes down that way when the client's keepalive gets no reply and every
queued transaction is terminated (4502). That lines up only by chance, so the bug shows rarely and
cannot be caught by cutting the network: with the app's network cut, WebRTC gathers no candidates,
and nothing is queued to fail.

The fix keeps a call that is up when a candidate is lost with the signaling: the ICE restart after
the reconnect gathers and sends a fresh set.

## Fault

The rule `close-trickle-after-update` closes the signaling exactly when the first candidate after an
`update` goes out - the moment the bug needs - with no change to the app.

The ICE restart itself comes from moving the phone from Wi-Fi to mobile data during the call. The
signaling stays up on the USB tunnel; the media path changes, so the app restarts ICE.

## Setup

- an Android phone with mobile data, on USB (`adb`), with the app connected through the proxy over
  `adb reverse` - see [Connecting the app](../connecting-the-app.md);
- a second client to call it (another phone, or the web app signed in to the same Core directly);
- the proxy running:

  ```sh
  uv run signaling-fault-proxy --upstream https://core.example.com --port 8080 --journal runs/wt2071.jsonl
  ```

## Steps

1. Call the phone from the second client, answer, check there is audio both ways.
2. Run the script - it arms the rule, switches the Wi-Fi off for 45 s and back on, and prints what the
   app did:

   ```sh
   scripts/android-ice-restart-on-wifi-off.sh <serial> 8080
   ```

   Or by hand:

   ```sh
   curl 'http://127.0.0.1:8080/__proxy/arm?rule=close-trickle-after-update'
   adb -s <serial> shell svc wifi disable
   # wait ~10 s, watch the call
   adb -s <serial> shell svc wifi enable
   curl 'http://127.0.0.1:8080/__proxy/lift'
   ```

## Expected

**The proxy**, with any build:

```
signaling connected from ('127.0.0.1', 52128)
close-trickle-after-update: closing the signaling on app -> core request:ice_trickle tx=transaction-4037598838
signaling closed (code 1006)
signaling connected from ('127.0.0.1', 52166)
```

**A build with the bug** ends the call itself right after the close:

```
INFO  SignalingModuleImpl - connect: starting new connect attempt (isConnected=false)
SEVERE CallBloc:SignalingErrorReporter - [__onMutationTrickleIce error] An error occurred: WebtritSignalingTransactionTerminateByDisconnectException ...
WARNING CallBloc - __onResetStateEventCompleteCall: <call id>
INFO  callkeep - InProcessCallkeepCore: Call ended by the app: <call id> (REMOTE_ENDED)
```

The call disappears on the phone; the other side gets a hang-up.

**A fixed build** keeps the call, reconnects and restarts ICE again:

```
INFO  SignalingModuleImpl - connect: starting new connect attempt (isConnected=false)
WARNING CallBloc - __onMutationTrickleIce: WebtritSignalingTransactionTerminateByDisconnectException, let call survive
INFO  SignalingModuleImpl - connect: starting new connect attempt (isConnected=false)
INFO  CallBloc - toCallEvent: AcceptedEvent
```

The call stays on both sides; audio continues after a short gap.

## Seen

2026-10-22, Pixel 9 (Android 17) on USB, the other side the web app; the same Core, the same steps:

| Build | Proxy | App |
|---|---|---|
| master before the fix | closed the signaling on the first candidate of the ICE restart (1006) | `__onMutationTrickleIce error` -> `completeCall`: the call ended |
| with the fix | the same | `let call survive` -> reconnect in 3 s -> ICE restart accepted: the call and audio kept |

## Notes

- Arm the rule shortly before switching the Wi-Fi: any `update` arms it, so an earlier hold or a
  re-INVITE from the other side would make it fire at the wrong moment.
- Switching the Wi-Fi back on causes a second ICE restart; the rule is one-shot and has lifted itself
  by then, so the second one passes untouched.
- This reproduces the effect of the keepalive loss (the candidate request terminated by the
  disconnect), not the keepalive timing itself.

#!/usr/bin/env bash
# Causes an ICE restart during a call on an Android phone connected through the proxy over USB
# (`adb reverse`) and shows what the app and the proxy did: arms a rule (close-trickle-after-update by
# default), switches the phone's Wi-Fi off so the media moves to mobile data, then back on.
#
#   scripts/android-ice-restart-on-wifi-off.sh <serial> [proxy port=8080] [rule=close-trickle-after-update] [seconds off=45]
#
# Start it with the call already up. The app's log is saved under runs/.
set -euo pipefail

SERIAL=${1:?usage: $0 <serial> [port] [rule] [seconds off]}
PORT=${2:-8080}
RULE=${3:-close-trickle-after-update}
OFF=${4:-45}
PROXY=http://127.0.0.1:$PORT/__proxy

cd "$(dirname "$0")/.."
mkdir -p runs
LOG=runs/app-$(date +%Y%m%d-%H%M%S).log

curl -fsS "$PROXY/status" >/dev/null || { echo "no proxy on port $PORT" >&2; exit 2; }
adb -s "$SERIAL" reverse --list | grep -q "tcp:$PORT" || {
  echo "no 'adb reverse tcp:$PORT tcp:$PORT' on $SERIAL - the app would not reach the proxy over USB" >&2
  exit 2
}

adb -s "$SERIAL" logcat -c
adb -s "$SERIAL" logcat -v time 'flutter:I' '*:S' > "$LOG" &
LOGCAT=$!
cleanup() {
  adb -s "$SERIAL" shell svc wifi enable >/dev/null 2>&1 || true
  curl -fsS "$PROXY/lift" >/dev/null 2>&1 || true
  kill "$LOGCAT" 2>/dev/null || true
}
trap cleanup EXIT

curl -fsS "$PROXY/arm?rule=$RULE" >/dev/null
echo "$(date +%T) armed $RULE; Wi-Fi off for ${OFF}s (the media moves to mobile data, the signaling stays on USB)"
adb -s "$SERIAL" shell svc wifi disable
sleep "$OFF"
echo "$(date +%T) Wi-Fi on; watching 30 s"
adb -s "$SERIAL" shell svc wifi enable
sleep 30
cleanup
trap - EXIT

echo "--- the app ($LOG)"
sed -E 's/\x1b\[[0-9;]*m//g' "$LOG" \
  | grep -aE "connect: starting|TrickleIce|GatheringComplete|let call survive|keeping the pending offer|__onResetStateEventCompleteCall|Call ended by the app|AcceptedEvent|HangupEvent" \
  | cut -c20-220 || true

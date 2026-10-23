#!/usr/bin/env bash
# Brings up the signup-as-login bench on one machine.
#
#   Core  <--  signaling-fault-proxy (:8411, --signup-as-login)  <--  browser client (:8422)
#
# By default the Core is the offline stub in this folder, so the bench runs with nothing but this
# repo and no credentials. To point it at a REAL Core, set the environment instead - no secret ever
# lives in this file:
#
#   SFP_UPSTREAM=https://core.example.com \
#   SFP_SIGNUP_AS_LOGIN=myaccount:mypassword \
#   testbench/run.sh
#
# Then open http://127.0.0.1:8422/client.html , type any email, press Submit.
# Ctrl-C stops everything.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"

PROXY_PORT="${PROXY_PORT:-8411}"
CLIENT_PORT="${CLIENT_PORT:-8422}"
FAKE_CORE_PORT="${FAKE_CORE_PORT:-9311}"

# Core + creds come from the environment. Unset -> the offline stub Core and its throwaway account.
UPSTREAM="${SFP_UPSTREAM:-http://127.0.0.1:$FAKE_CORE_PORT}"
LOGIN="${SFP_SIGNUP_AS_LOGIN:-testuser:secret}"
USING_STUB=0; [ "$UPSTREAM" = "http://127.0.0.1:$FAKE_CORE_PORT" ] && USING_STUB=1

pids=()
cleanup() { kill "${pids[@]}" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

if [ "$USING_STUB" = 1 ]; then
  echo "== offline stub Core on :$FAKE_CORE_PORT"
  python3 "$HERE/fake_core.py" --port "$FAKE_CORE_PORT" & pids+=($!)
  sleep 0.5
else
  echo "== Core: $UPSTREAM  (from SFP_UPSTREAM)"
fi

echo "== proxy on :$PROXY_PORT  (signup -> login as ${LOGIN%%:*})"
( cd "$REPO" && SFP_UPSTREAM="$UPSTREAM" SFP_SIGNUP_AS_LOGIN="$LOGIN" \
    uv run signaling-fault-proxy --port "$PROXY_PORT" ) & pids+=($!)
sleep 1.5

echo "== client on :$CLIENT_PORT"
python3 -m http.server "$CLIENT_PORT" --directory "$HERE" --bind 127.0.0.1 & pids+=($!)
sleep 0.5

echo
echo "  open  ->  http://127.0.0.1:$CLIENT_PORT/client.html"
echo
wait

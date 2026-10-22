# signaling-fault-proxy

A reverse proxy between the [WebTrit](https://github.com/WebTrit/webtrit_phone) app and its Core
that breaks the signaling on purpose - by rule, at the exact message a bug needs, instead of by a
timer or by cutting the phone's network.

The app and Core are not changed. The app is built with the proxy as its Core; the proxy forwards
everything to the real one, logs every signaling frame, and applies the rules you arm.

```
 phone / browser (app, Core = http://127.0.0.1:8080)
        |
        |   Android: adb reverse over USB - the signaling does not depend on the phone's network
        v
 signaling-fault-proxy :8080  ------>  https://<your Core>   (HTTP API and the /signaling/v1 WebSocket)

 media: phone <-----------------------> Janus / TURN of that Core   (never through the proxy)
```

The media addresses come in the SDP from Core, so the media goes around the proxy. That is what makes
the proxy useful: a test can break the media and the signaling separately, at the moment it chooses.

## Quick start

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/SERDUN/signaling-fault-proxy.git
cd signaling-fault-proxy
uv sync
uv run signaling-fault-proxy --upstream https://core.example.com --port 8080 --journal runs/journal.jsonl
```

Then point the app at `http://127.0.0.1:8080` - see [Connecting the app](docs/connecting-the-app.md) -
and sign in with the upstream Core's accounts as usual.

Arm a rule while the app runs:

```sh
curl 'http://127.0.0.1:8080/__proxy/arm?rule=close-trickle-after-update'
curl 'http://127.0.0.1:8080/__proxy/status'
curl 'http://127.0.0.1:8080/__proxy/lift'
```

## Rules

Most faults need no code - generic rules match frames by direction, kind, name and the frame before
them:

```sh
curl 'http://127.0.0.1:8080/__proxy/arm?rule=close-on&from=app&name=hold&after=update'
curl 'http://127.0.0.1:8080/__proxy/arm?rule=drop-on&from=core&name=keepalive&times=0'
```

| Rule | What it does | Lifts |
|---|---|---|
| `close-on` | closes the connection on the matching frame (`from`, `kind`, `name`, `after`, `times`) | after `times` frames (1); `times=0` - when lifted |
| `drop-on` | drops the matching frames, the connection stays | the same |
| `close-trickle-after-update` | after the app sends `update` (an ICE restart offer, say), the first candidate it trickles closes the connection before the candidate reaches Core | itself, once fired |
| `blackhole` | a silent loss of the signaling: every frame both ways is dropped, nothing is closed, requests wait | when lifted |
| `close-now` | closes every signaling connection on its next frame, either way | itself, once fired |

A rule of your own is a small Python class in your test project, loaded with `--rules my_rules.py` -
no change to the proxy. "Close" means both sides of the TCP connection are closed at once with no
WebSocket close frame - the app sees an abnormal closure (1006), as when a socket goes. Parameters,
examples and writing a rule: [docs/rules.md](docs/rules.md).

## Control API

Served on the proxy's own port and never forwarded to Core:

| Request | Answer |
|---|---|
| `GET /__proxy/status` | upstream, armed rules, live signaling connections, known rules |
| `GET /__proxy/arm?rule=<name>[&<param>=<value>...]` | arms a rule with its parameters; `400` for an unknown rule or parameter |
| `GET /__proxy/lift` | lifts every rule |
| `GET /__proxy/lift?rule=<name>` | lifts one rule |

Rules can also be armed at start: `--arm 'close-trickle-after-update,drop-on?name=hold'`.

## Logs

Every signaling frame is printed as it passes, and with `--journal <file>` appended as a JSON line:

```
app -> core request:update tx=transaction-35762023710
core -> app response:ack tx=transaction-35762023710
app -> core request:ice_trickle tx=transaction-4037598838  [close by close-trickle-after-update]
```

```json
{"t": 1792663816.426, "from_client": true, "kind": "request", "name": "ice_trickle", "transaction": "transaction-4037598838", "action": "close", "rule": "close-trickle-after-update"}
{"t": 1792663816.427, "note": "signaling closed (code 1006)"}
```

The journal of a run is the proxy's side of the evidence; the app's log is the other.

## Reproductions

Step-by-step recipes with the expected result on a build with the bug and on a fixed one, made to be
linked from a review or a ticket - the list and how to add one: [docs/reproduce](docs/reproduce/README.md).

- [Lost trickle request after an ICE restart ends the call](docs/reproduce/lost-trickle-after-ice-restart.md)
  (WT-2071) - script: [`scripts/android-ice-restart-on-wifi-off.sh`](scripts/android-ice-restart-on-wifi-off.sh)

## Options

```
signaling-fault-proxy --upstream URL [--host 127.0.0.1] [--port 8080] [--arm RULES] [--rules FILES] [--journal FILE] [-- MITMDUMP_ARGS...]
```

| Option | Default | Meaning |
|---|---|---|
| `--upstream` | (required) | the Core to forward to, `https://...` or `http://...` |
| `--host` | `127.0.0.1` | address to listen on; `0.0.0.0` to reach it from another device on the LAN |
| `--port` | `8080` | port to listen on |
| `--arm` | | rules to arm at start, `name[?param=value&...]`, comma-separated |
| `--rules` | | Python files with rules of your own, comma-separated |
| `--journal` | | file to append every signaling frame to (JSON lines) |
| `-- ...` | | passed to `mitmdump` as is |

## Develop

```sh
uv sync
uv run pytest
uv run ruff check && uv run ruff format --check
```

The proxy is a [mitmproxy](https://mitmproxy.org/) addon (`src/signaling_fault_proxy/addon.py`) run by
`mitmdump` in reverse-proxy mode; the rules (`rules.py`) know nothing of mitmproxy and are unit-tested
on their own.

## License

[MIT](LICENSE)

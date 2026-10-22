"""signaling-fault-proxy --upstream https://<core> [--port 8080] [--arm rule,...] [--journal file]

Starts mitmdump as a reverse proxy to the upstream Core with the addon loaded. Any extra arguments
after `--` go to mitmdump as they are.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path
from urllib.parse import urlparse

from signaling_fault_proxy import addon


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="signaling-fault-proxy", description=__doc__.splitlines()[0])
    parser.add_argument(
        "--upstream", required=True, help="the Core to forward to, e.g. https://core.example.com"
    )
    parser.add_argument("--host", default="127.0.0.1", help="address to listen on (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8080, help="port to listen on (default 8080)")
    parser.add_argument(
        "--arm", default="", help="rules to arm at start: name[?param=value&...], comma-separated"
    )
    parser.add_argument("--rules", default="", help="python files with more rules, comma-separated")
    parser.add_argument("--journal", default="", help="file to append every signaling frame to")
    parser.add_argument("extra", nargs=argparse.REMAINDER, help="arguments for mitmdump after --")
    args = parser.parse_args(argv)

    upstream = urlparse(args.upstream)
    if upstream.scheme not in ("http", "https") or not upstream.hostname:
        parser.error("--upstream must be an http(s) URL of the Core")

    mitmdump = shutil.which("mitmdump") or str(Path(sys.executable).with_name("mitmdump"))
    extra = args.extra[1:] if args.extra[:1] == ["--"] else args.extra
    command = [
        mitmdump,
        "--mode",
        f"reverse:{args.upstream.rstrip('/')}",
        "--listen-host",
        args.host,
        "--listen-port",
        str(args.port),
        "--set",
        "connection_strategy=lazy",
        "--set",
        "flow_detail=0",
        "-s",
        addon.__file__,
        "--set",
        f"arm={args.arm}",
        "--set",
        f"journal={args.journal}",
        "--set",
        f"rules={','.join(str(Path(p).resolve()) for p in args.rules.split(',') if p)}",
        *extra,
    ]
    print(f"forwarding http://{args.host}:{args.port} -> {args.upstream}", file=sys.stderr)
    os.execv(command[0], command)
    return 0  # not reached


if __name__ == "__main__":
    raise SystemExit(main())
